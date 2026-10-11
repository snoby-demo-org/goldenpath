#!/usr/bin/env python3
"""Backstage adoption metrics for snoby-demo-org.

Measures the portal->OS shift with numbers (the thing the article forgot):
  1. Org repos vs onboarded repos (catalog-info.yaml present)
  2. TechDocs pipeline coverage: onboarded repos whose publish workflow is the
     centralized reusable caller vs drift (own inline publish logic)
  3. Secrets health: onboarded repos missing repo-level TECH_DOCS_* secrets
  4. Live catalog entity counts (by kind) from the Backstage REST API
  5. Onboarded components with a live published docs site

Writes a markdown block to stdout; --wiki updates the wiki page over SSH.
Auth: gh CLI for GitHub; Backstage MCP token parsed from Hermes config (never
stored in this file).
"""
import base64
import json
import os
import subprocess
import sys
import urllib.request
import ssl

ORG = "snoby-demo-org"
BACKSTAGE = "https://backstage.viporlab.net"
HERMES_CONFIG = os.path.expanduser(
    "~/.hermes/profiles/ethpow/config.yaml")
WIKI_HOST = "snoby@10.0.0.53"
WIKI_PAGE = "~/wiki/entities/backstage-adoption-metrics.md"
RUNNER_REUSABLE = "techdocs-reusable.yml"


def gh(endpoint):
    out = subprocess.run(
        ["gh", "api", endpoint], capture_output=True, text=True)
    if out.returncode != 0:
        return None
    return json.loads(out.stdout)


def file_exists(repo, path):
    r = gh(f"repos/{ORG}/{repo}/contents/{path}")
    if not isinstance(r, dict):
        return False
    return r.get("type") == "file"


def get_file(repo, path):
    r = gh(f"repos/{ORG}/{repo}/contents/{path}")
    if not isinstance(r, dict) or r.get("type") != "file":
        return None
    return base64.b64decode(r["content"]).decode()


def backstage_token():
    import re
    s = open(HERMES_CONFIG).read()
    m = re.search(
        r"^  backstage:\n(?:.*\n)*?      Authorization: Bearer (\S+)", s, re.M)
    if not m:
        raise SystemExit("no backstage bearer token in hermes config")
    return m.group(1)


def backstage_get(path, token):
    req = urllib.request.Request(
        f"{BACKSTAGE}{path}", headers={"Authorization": f"Bearer {token}"})
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
        return json.loads(resp.read())


def collect():
    repos = [r["name"] for r in gh(f"orgs/{ORG}/repos?per_page=100") or []]
    rows = []
    for repo in sorted(repos):
        onboarded = file_exists(repo, "catalog-info.yaml")
        wf_path = ".github/workflows/techdocs.yml"
        wf = get_file(repo, wf_path) if onboarded else None
        uses_reusable = bool(wf) and RUNNER_REUSABLE in wf
        inline_publish = bool(wf) and not uses_reusable and "techdocs-cli publish" in wf
        no_wf = onboarded and wf is None
        secrets = []
        if onboarded:
            s = gh(f"repos/{ORG}/{repo}/actions/secrets?per_page=100") or {}
            names = {x["name"] for x in s.get("secrets", [])}
            secrets = [n for n in
                       ("TECH_DOCS_ACCESS_KEY", "TECH_DOCS_SECRET_KEY",
                        "TECH_DOCS_S3_ENDPOINT") if n not in names]
        rows.append(dict(repo=repo, onboarded=onboarded,
                         uses_reusable=uses_reusable,
                         inline_publish=inline_publish, no_wf=no_wf,
                         missing_secrets=secrets))

    token = backstage_token()
    facets = backstage_get("/api/catalog/entity-facets?facet=kind", token)
    kinds = {f["value"]: f["count"] for f in facets["facets"]["kind"]}
    comps = backstage_get(
        "/api/catalog/entities?filter=kind=component"
        "&fields=metadata.name,metadata.annotations", token)
    items = comps if isinstance(comps, list) else comps.get("items", [])
    comp_docs = [
        c["metadata"]["name"] for c in items
        if (c["metadata"].get("annotations") or {}).get("backstage.io/techdocs-ref")
    ]
    return rows, kinds, comp_docs


def render(rows, kinds, comp_docs, ts):
    L = []
    total = len(rows)
    onboarded = [r for r in rows if r["onboarded"]]
    centralized = [r for r in onboarded if r["uses_reusable"]]
    drifted = [r for r in onboarded if r["inline_publish"]]
    no_wf = [r for r in onboarded if r["no_wf"]]
    bad_secrets = [r for r in rows if r["missing_secrets"]]
    L.append(f"### Backstage adoption metrics — {ts}")
    L.append("")
    L.append("| Metric | Value |")
    L.append("|---|---|")
    L.append(f"| Org repos (total) | {total} |")
    L.append(f"| Onboarded (catalog-info.yaml) | {len(onboarded)} |")
    cov = f"{100*len(centralized)//max(len(onboarded),1)}%" if onboarded else "n/a"
    L.append(f"| TechDocs: centralized (reusable caller) | {len(centralized)} ({cov} of onboarded) |")
    L.append(f"| TechDocs: drifted (inline publish logic) | {len(drifted)} |")
    L.append(f"| TechDocs: onboarded, no publish workflow | {len(no_wf)} |")
    L.append(f"| Onboarded, missing repo secrets | {len(bad_secrets)} |")
    L.append(f"| Catalog components | {kinds.get('Component', 0)} |")
    L.append(f"| Catalog systems | {kinds.get('System', 0)} |")
    L.append(f"| Catalog templates | {kinds.get('Template', 0)} |")
    L.append(f"| Catalog APIs | {kinds.get('API', kinds.get('api', 0))} |")
    L.append(f"| Components with techdocs-ref | {len(comp_docs)} |")
    L.append("")
    detail = []
    for r in rows:
        if not r["onboarded"]:
            continue
        state = ("centralized" if r["uses_reusable"]
                 else "DRIFTED-inline" if r["inline_publish"]
                 else "NO-WORKFLOW")
        if r["missing_secrets"]:
            state += f" +missing-secrets({len(r['missing_secrets'])})"
        detail.append(f"- `{r['repo']}`: {state}")
    if detail:
        L.append("<details><summary>Per-repo detail</summary>")
        L.append("")
        L.extend(detail)
        L.append("")
        L.append("</details>")
    return "\n".join(L) + "\n"


def write_wiki(md):
    body = """---
title: Backstage Adoption Metrics
created: 2026-10-10
updated: %s
type: entity
tags: [service, k8s, reference]
sources: []
confidence: high
---

# Backstage Adoption Metrics

Portal->OS shift measured by numbers (snoby-demo-org + backstage.viporlab.net
catalog). Refreshed nightly by the agent (Hermes cronjob `backstage-adoption-metrics`).

- **Centralized** = repo's TechDocs publish workflow calls goldenpath's
  `techdocs-reusable.yml` (single source of truth; a fix lands once for all).
- **DRIFTED-inline** = repo carries its own copy of the publish logic.
- Coverage target: all onboarded repos centralized; drift back to 0.

%s

## History

See `log.md` for when the reusable workflow landed and repos were migrated.
""" % (ts_date(), md)
    cmd = ["ssh", "-o", "BatchMode=yes", WIKI_HOST,
           f"cat > {WIKI_PAGE}", ]
    subprocess.run(cmd, input=body, text=True, check=True)


def ts_date():
    import datetime
    return datetime.datetime.now().strftime("%Y-%m-%d")


if __name__ == "__main__":
    import datetime
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    rows, kinds, comp_docs = collect()
    md = render(rows, kinds, comp_docs, ts)
    if "--wiki" in sys.argv:
        write_wiki(md)
        print("wiki updated:", WIKI_PAGE)
    print(md)
