#!/usr/bin/env python3
"""TrueNorth enforcement core — FR-58..FR-61, FR-13, FR-71.

The check model's teeth live here, in pure functions, so the enforcement
behavior is UNIT-TESTABLE without GitHub, dagger, or a runner (see tests/):

  required failure  -> block (hard stop; never overridable, even by exception)
  required disabled -> block (FR-59 hard stop)
  blocking failure  -> block unless a declared, unexpired exception releases it
  advisory failure  -> flag + error budget; the pipeline continues (FR-59/60)
  no results        -> block (config invalid or nothing ran)

A failing check's build errors (exit_code, findings) ride on its block/flag
entry, so the report shows WHAT broke, not just that something broke.

The workflow's build-report job calls this file as a CLI
(python3 .github/truenorth_report.py); the check-leg harnesses import
build_result / write_result / harness_exit_code from it, so the check
contract and the exit decision each have exactly one implementation.
"""
import datetime
import glob
import json
import os
import sys

MODES = ("required", "blocking", "advisory")
BUDGET_ZERO = {"advisory_findings": 0, "out_of_policy_exceptions": 0,
               "disabled_required": 0, "total": 0}


def harness_exit_code(mode, returncode):
    """Exit decision for a check leg (FR-59/60): an advisory check that found
    build errors records them and exits GREEN so the pipeline continues;
    required/blocking failures propagate their exit code (job goes red)."""
    if mode == "advisory" and returncode != 0:
        return 0
    return returncode


def build_result(check, cfg, returncode, duration_ms, run_id, commit, job,
                 score=None, findings=None, details=None):
    """Check contract (FR-7/FR-11): the structured result every check leg emits."""
    return {
        "check": check,
        "status": "pass" if returncode == 0 else "fail",
        "mode": cfg.get("mode", "blocking"),
        "tier": cfg.get("tier", "fast"),
        "score": score, "findings": findings,
        "duration_ms": duration_ms, "exit_code": returncode,
        "commit": commit, "run_id": run_id, "job": job,
        "details": details if details is not None else {},
    }


def write_result(result, stdout_text, outdir="results"):
    os.makedirs(outdir, exist_ok=True)
    check = result["check"]
    with open(os.path.join(outdir, f"{check}.json"), "w") as f:
        json.dump(result, f)
    with open(os.path.join(outdir, f"{check}.stdout.log"), "w") as f:
        f.write(stdout_text)


def load_exceptions(path=".github/truenorth-exceptions.yaml"):
    import yaml  # lazy: the pure functions must work without pyyaml installed
    try:
        return (yaml.safe_load(open(path)) or {}).get("exceptions", [])
    except FileNotFoundError:
        return []


def _exc_for(exceptions, check, today):
    for e in exceptions:
        if e.get("check") == check and str(e.get("until", "0000-00-00")) >= today:
            return e
    return None


def conclusion_of(blocks, flags):
    return "blocked" if blocks else ("flagged" if flags else "clean")


def classify(checks_cfg, results, exceptions=(), today=None):
    """The run-level truth: blocks vs flags vs clean + the error budget.

    checks_cfg: parsed truenorth.yaml checks section ({name: {enabled, mode, ...}})
    results:    {check: contract JSON} as emitted by the check-leg harnesses
    exceptions: list of {check, until, approver, reason} from truenorth-exceptions.yaml
    """
    today = today or datetime.date.today().isoformat()
    blocks, flags, clean = [], [], []
    budget = dict(BUDGET_ZERO)

    if not results:
        blocks.append({"check": "pipeline", "type": "no_results",
                       "reason": "no check produced a result — config invalid or nothing ran"})

    for name, c in checks_cfg.items():
        mode = c.get("mode", "blocking")
        if not c.get("enabled", True):
            if mode == "required":
                blocks.append({"check": name, "type": "disabled_required",
                               "reason": "required check disabled — hard stop (FR-59)"})
                budget["disabled_required"] += 1
            continue
        r = results.get(name)
        if r is None:
            if mode == "required":
                blocks.append({"check": name, "type": "missing_required",
                               "reason": "required check did not produce a result (FR-59)"})
            continue
        if r.get("status") == "pass":
            clean.append({"check": name, "duration_ms": r.get("duration_ms")})
            continue

        # A failing check's build errors ride on its block/flag entry.
        err = {k: r[k] for k in ("exit_code", "findings") if r.get(k) is not None}

        if mode == "required":
            block = {"check": name, "type": "required_failure",
                     "reason": "required check failed — no exception path (FR-71)"}
            block.update(err)
            blocks.append(block)
            if _exc_for(exceptions, name, today):
                flags.append({"check": name, "type": "ignored_exception",
                              "reason": "exception present but required checks are never overridable (FR-71)"})
        elif mode == "blocking":
            e = _exc_for(exceptions, name, today)
            if e:
                flags.append({"check": name, "type": "out_of_policy_exception",
                              "reason": f"blocking failure released under exception by {e.get('approver', '?')}: {e.get('reason', '')} (until {e.get('until')})"})
                budget["out_of_policy_exceptions"] += 1
            else:
                block = {"check": name, "type": "blocking_failure",
                         "reason": "blocking check failed (FR-59)"}
                block.update(err)
                blocks.append(block)
        else:
            flag = {"check": name, "type": "advisory_finding",
                    "reason": "advisory check failed — feeds error budget, does not block (FR-59)"}
            flag.update(err)
            flags.append(flag)
            budget["advisory_findings"] += 1

    budget["total"] = (budget["advisory_findings"] + budget["out_of_policy_exceptions"]
                       + budget["disabled_required"])
    return {"blocks": blocks, "flags": flags, "clean": clean,
            "error_budget": budget, "conclusion": conclusion_of(blocks, flags)}


def main():
    import yaml  # lazy: see load_exceptions

    run_id = int(os.environ["GITHUB_RUN_ID"])
    commit = os.environ["GITHUB_SHA"]
    repo = os.environ["GITHUB_REPOSITORY"]

    cfg = yaml.safe_load(open(".github/truenorth.yaml"))
    checks_cfg = cfg.get("checks", {})
    exceptions = load_exceptions()

    results = {}
    for p in glob.glob("results/*.json"):
        try:
            r = json.load(open(p))
            results[r["check"]] = r
        except (json.JSONDecodeError, KeyError):
            print(f"::warning::unparseable check result: {p}")

    report = classify(checks_cfg, results, exceptions)

    # Pipeline-level block that is not a check result: if the enforcement
    # unit tests failed, the gates themselves are unverified — a red gate
    # must not mint a green conclusion.
    unit = os.environ.get("TN_UNIT_RESULT", "")
    if unit and unit not in ("success", "skipped"):
        report["blocks"].append({"check": "pipeline", "type": "unit_tests_failed",
                                 "reason": f"pipeline unit tests {unit} — enforcement model unverified"})
        report["conclusion"] = conclusion_of(report["blocks"], report["flags"])

    blocks, flags, clean = report["blocks"], report["flags"], report["clean"]
    budget = report["error_budget"]
    conclusion = report["conclusion"]

    report_out = {
        "run_id": run_id, "commit": commit, "repo": repo,
        "conclusion": conclusion,
        "blocks": blocks, "flags": flags, "clean": clean,
        "error_budget": budget,
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }

    # CIT handoff payload (FR-25/26): artifact metadata + checks truth +
    # budget flag, structured for the webhook step and a future deploy stage.
    build_r = results.get("build") or {}
    handoff = {
        "release": {"repo": repo, "commit": commit, "version": f"sha-{commit[:8]}"},
        "artifact": {
            "registry": os.environ.get("TN_REGISTRY", cfg.get("pipeline", {}).get("registry", "")),
            "digest": (build_r.get("details") or {}).get("digest"),
        },
        "checks": {"passed": [c["check"] for c in clean],
                   "blocked": [b["check"] for b in blocks],
                   "flagged": [f["check"] for f in flags]},
        "error_budget": budget,
        "conclusion": conclusion,
    }

    os.makedirs("report", exist_ok=True)
    json.dump(report_out, open("report/build-report.json", "w"), indent=2)
    json.dump(handoff, open("report/cit-handoff.json", "w"), indent=2)

    print(json.dumps(report_out, indent=2))
    # Flat budget_total on the log line so LogQL can unwrap it as a metric
    rline = dict(report_out)
    rline["budget_total"] = budget["total"]
    # tn_conclusion: the stream already carries a job-level `conclusion`
    # LABEL from the log shipper, which would shadow the parsed JSON
    # field in LogQL group-bys — so the run conclusion gets its own name.
    rline["tn_conclusion"] = conclusion
    print("TRUENORTH_BUILD_REPORT " + json.dumps(rline, separators=(",", ":")), flush=True)

    # Run summary table (visible on the run page)
    with open(os.environ["GITHUB_STEP_SUMMARY"], "w") as f:
        f.write("## TrueNorth build report\n\n")
        f.write(f"**Conclusion:** `{conclusion}` — error budget consumed: **{budget['total']}**\n\n")
        f.write("| Check | Mode | Status | Duration | Verdict |\n|---|---|---|---|---|\n")
        for name, c in checks_cfg.items():
            if not c.get("enabled", True):
                f.write(f"| {name} | {c.get('mode')} | disabled | — | — |\n")
                continue
            r = results.get(name)
            if r is None:
                f.write(f"| {name} | {c.get('mode')} | no-result | — | — |\n")
                continue
            dur = f"{r.get('duration_ms', 0)/1000:.1f}s"
            verdict = "clean"
            if any(b["check"] == name for b in blocks):
                verdict = "BLOCK"
            elif any(fl["check"] == name for fl in flags):
                verdict = "FLAG"
            f.write(f"| {name} | {c.get('mode')} | {r.get('status')} | {dur} | {verdict} |\n")
        unit = os.environ.get("TN_UNIT_RESULT", "")
        if unit:
            verdict = {"success": "clean", "skipped": "—"}.get(unit, "BLOCK")
            f.write(f"| unit-tests | pipeline | {unit} | — | {verdict} |\n")
        if blocks:
            f.write("\n**BLOCKS (hard stop):**\n")
            for b in blocks:
                f.write(f"- `{b['check']}` — {b['reason']}\n")
        if flags:
            f.write("\n**FLAGS (error budget):**\n")
            for fl in flags:
                f.write(f"- `{fl['check']}` — {fl['reason']}\n")

    # Hard stop: blocks fail the job (FR-59: blocking failures block;
    # FR-71: secrets/PII are never overridable).
    for b in blocks:
        print(f"::error::BLOCK {b['check']}: {b['reason']}")
    if blocks:
        sys.exit(1)


if __name__ == "__main__":
    main()
