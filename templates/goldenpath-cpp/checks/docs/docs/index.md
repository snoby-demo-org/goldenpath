# ${{ values.componentId }}

${{ values.description }}

## Overview

This component was onboarded into Backstage via the **golden path** template,
which added the TrueNorth enforcement pipeline and documentation to this
existing project.

## Repository layout

| Path | Purpose |
|------|---------|
| `.github/truenorth.yaml` | The ONLY pipeline file this repo owns — check selection, required/blocking/advisory modes, declared build hooks + build matrix |
| `.github/workflows/dagger-checks.yml` | Thin caller pinned to the shared goldenpath TrueNorth machinery (`truenorth-v2.2`) |
| `.github/workflows/techdocs.yml` | Publish these docs to Backstage TechDocs (MinIO) |
| `catalog-info.yaml` | Backstage Component entity (this repo's registry entry) |
| `docs/` | This documentation |

## Pipeline

Every push/PR runs the TrueNorth machinery on the self-hosted ARC runner:
secrets/PII/private-key scans are **required** (never disableable); the build
leg compiles the project in a pinned language toolchain container via
**declared hooks**; lint/sast/format start **advisory** and can be flipped to
blocking on your own schedule. Enforcement results land as
`TRUENORTH_RESULT` payload fields, visible in the goldenpath-builds Grafana
dashboard (logs in Loki, traces in Tempo).

To change what runs, edit `.github/truenorth.yaml` only — no workflow change
(NFR-2). To get new machinery, bump the `truenorth-vN` pin in
`.github/workflows/dagger-checks.yml` (FR-14).

## Build

The build leg runs the hooks declared in `.github/truenorth.yaml` inside the
language toolchain container. Keep them in sync with the local build below.
