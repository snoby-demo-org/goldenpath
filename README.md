# crypto-node-templates

Backstage **software templates** for scaffolding crypto node deployments.

Discovered via Backstage's catalog from this repo's `catalog-info.yaml`.

## Templates

- [`templates/civicnet-node/`](templates/civicnet-node/) — **CivicNet (CIVIC) node**:
  scaffolds a `civicnetd` daemon repo with config, systemd unit, healthcheck,
  and README. Generates and *pushes* a fresh private GitHub repo.
- [`templates/crypto-node/`](templates/crypto-node/) — **generic crypto node**:
  same scaffold, parameterized for any coin (vendored source + patches +
  Dockerfile + build.sh + systemd).
- [`templates/truenorth/`](templates/truenorth/) — **TrueNorth enforcement
  pipeline**: config-driven checks (`.github/truenorth.yaml` is the only file
  the repo owns), ARC runner planes, Loki/Tempo telemetry. Build-leg control
  (hook slots & matrix variants): [`docs/truenorth-build-contract.md`](docs/truenorth-build-contract.md).

## How it works

Using a template in Backstage (`/create`) walks you through a form, then runs
the scaffolder actions (`fetch:template` → `publish:github` → `catalog:register`).
The result is a new private repo under `snoby/` registered in the catalog.

During scaffolding you **select which GitHub Actions workflows** the new repo
gets (Build & push image, Validate YAML, Dagger lint + PII, Smoke test). The
image push target is also selectable (`ghcr` = zero-secret per-repo GHCR
packages, `dockerhub` = Docker Hub via DOCKERHUB secrets).

## Structure

```
catalog-info.yaml                 # catalog Location -> discovers templates
templates/
  <name>/
    template.yaml                 # Backstage Template entity (v1beta3)
    skeleton/                     # files scaffolded into new repos (Nunjucks)
    checks/                       # optional partial skeletons (GitHub Actions)
      build-node/                 #   build+push node image
      ci/                         #   YAML validation gate
      dagger-checks/              #   lint + PII scan (shared Dagger engine)
      smoke/                      #   build+boot node + healthcheck
```
