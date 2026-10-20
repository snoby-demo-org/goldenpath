# ${{ values.componentId }} — TrueNorth pipeline

${{ values.description }}

Scaffolded from the **TrueNorth** Backstage template. The repo runs the
TrueNorth enforcement pipeline: config-driven checks where
`.github/truenorth.yaml` is the **only file you own** to drive CI.

## How it works

- `.github/truenorth.yaml` — check selection + enforcement modes:
  - `required` — always on, hard-stops the run on failure, never overridable (PII, secrets)
  - `blocking` — failure blocks the run unless a declared exception exists
    (`.github/truenorth-exceptions.yaml`, auditable, expires)
  - `advisory` — failure flags + feeds the error budget; the run stays green
  - `tier: heavy` — runs only on deep-scan triggers (dispatch input, commit
    trailer `Deep-Scan: true`, PR label `deep-scan`)
- `.github/workflows/dagger-checks.yml` — the generic pipeline: every enabled
  check is a parallel ARC runner pod on its plane; `build` produces the image
  digest handoff for the test plane; `build-report` aggregates everything
- `.github/truenorth_report.py` + `tests/` — the enforcement gates are
  **unit-tested on every push**; the build report blocks the run if the tests
  fail, so a broken gate can't mint a green conclusion
- Every check emits `TRUENORTH_CHECK_RESULT` + `TRUENORTH_CONFIG` + a run-level
  `TRUENORTH_BUILD_REPORT` log line — the lab Grafana stack
  (`goldenpath-builds` dashboard) renders checks, traces, and the flow from
  that telemetry

## Controlling the build (hooks)

The build check runs `patch → configure → build` in a pinned toolchain
container. You control it with files in your repo root (presence-detected,
no config needed):

- `patches/*.patch` — applied in glob order with `patch -p1` before anything else
- `autogen.sh` — optional prep/regeneration stage, runs before configure
- `build.sh` — if present, it **owns the whole build**; otherwise the
  pipeline runs `./configure && make`

The check fails on a failed stage or a missing output binary, reporting the
failing stage in `TRUENORTH_RESULT`. Multiple build targets (matrix, e.g.
per-CPU-arch) are declared under `build:` in `.github/truenorth.yaml` —
contract: goldenpath TechDocs, *"TrueNorth build contract"*.

## Adding a check

1. Implement a function with the check's name in the shared Dagger module
   (`${{ values.daggerModule }}`) — it must exit non-zero on findings and may
   print `TRUENORTH_RESULT: {json}` for score/findings enrichment
2. Add an entry under `checks:` in `.github/truenorth.yaml` with the mode,
   tier, and plane you want — the workflow fans it out automatically

## Local unit tests

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
```
