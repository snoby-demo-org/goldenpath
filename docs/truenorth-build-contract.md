# TrueNorth build contract: hooks & matrix

How a repo controls the **build leg** of the TrueNorth pipeline. The machinery
lives once — pinned reusable workflow (`goldenpath/truenorth.yml@vN`) plus the
language Dagger module (`dagger-checks-c@v1.1.0` for C/C++). What a repo owns
is its **knowledge**: `.github/truenorth.yaml` and the hook files described
here (Option B: machinery pinned by tag, recipes owned by the repo).

## The build contract

The C/C++ build check runs inside a pinned `ubuntu:24.04` toolchain container
(build-essential, autoconf, libcurl, libjansson, openssl, libgmp, libhwloc,
ccache). The actual stage flow is:

```
patch → configure-or-build.sh → make
```

`make -j"$(nproc)"` **always runs last, in both paths**. Each stage has a
hook slot. **Hooks are files in the repo root** — drop them in and the
pipeline uses them; no config required.

### Stage flow and hook slots

| # | Stage | Hook (presence-detected) | Behavior | Failure status → failed_stage |
|---|---|---|---|---|
| 1 | patch | `patches/*.patch` | Applied in glob order with `patch -p1` before anything else | `patch_failed` → `patch` |
| 2 | configure | `autogen.sh` + `./configure` — *without* `build.sh` | `autogen.sh` runs first if present (a fresh clone's shipped `configure` can be stale vs `Makefile.am`), then `./configure` runs | `autogen_failed`, `configure_failed` → `configure`; `no_configure` only when neither `autogen.sh` nor an executable `./configure` exists |
| 2 | build | `build.sh` — *with* `build.sh` | **Replaces the generic configure stage**: `autogen.sh` pre-stage (if present) → `build.sh` | `autogen_failed`, `build_failed` → `build.sh` |
| 3 | make | — (no hook) | `make -j"$(nproc)"` (or `-j<jobs>` when the workflow passes `jobs`) — runs last in **both** paths | `make_failed` → `make` |

Rules:

- **Presence detection is the default.** No `truenorth.yaml` change needed to
  use a hook.
- **`build.sh` replaces only the configure step — `make` still runs after
  it.** A `build.sh` that leaves the tree unmakeable (distclean, no default
  target) will pass its own stage and then die with `make_failed`.
  (`cpuminer-opt-cpupower` builds this way — znver2 flags, static libstdc++,
  versioned HiveOS packaging — and works because the tree is left
  make-ready; see [Known wart and follow-up](#known-wart-and-follow-up).)
- Quirk: an `autogen.sh` failure inside the `build.sh` path reports
  `failed_stage: "build.sh"` (the stage is selected before the pre-stage
  runs), and `no_binary` reports `failed_stage: "make"` (the binary check
  runs after the make stage).
- The check **fails** on any stage failure or when no ELF binary is
  produced. The failing stage's last 60 log lines are surfaced in the error;
  log-tail selection is by failed stage name (`make`/`build.sh` →
  `make.log`, `configure` → `configure.log`, anything else → the console
  log, which also serves as fallback when the stage log does not exist).
- Compiler warnings are counted and reported; they do **not** fail the check.

### Result contract (FR-7)

Emitted as a `TRUENORTH_RESULT: {json}` log line **on PASS only** — on
failure the module raises with the failing stage's log tail instead:

```json
{"check":"build","tool":"autotools@ubuntu:24.04",
 "status":"pass","failed_stage":null,"warnings":2,
 "binary":"cpuminer version 3.9.1 (posix) [clang ...]"}
```

- `status` is only `pass` or `fail`. The internal `*_failed` strings appear
  in console `[build] status=...` lines, **not** in the payload.
- On failure `failed_stage` is one of `patch` / `configure` / `build.sh` /
  `make`; `null` on pass.
- `binary` is the first line of the detected binary's `--version` output —
  not a path. Detection: an executable `cpuminer*` / `minerd*` file within
  `maxdepth 2` whose first 4 bytes are ELF magic `7f454c46`; no match →
  failure with `no_binary`.

### Declared hooks (contract — machinery pending)

Presence detection stays the default, but hooks may be declared explicitly.
Explicit beats magic, and **a declared hook that does not exist must fail
fast at config parse** — not 20 minutes into `make`:

```yaml
build:
  enabled: true
  mode: blocking
  module: github.com/snoby-demo-org/dagger-checks-c@v1.1.0
  hooks:
    patch:     patches/*.patch   # glob, applied with patch -p1
    configure: autogen.sh        # prep / regeneration stage
    build:     build.sh          # replaces ./configure; make still runs
```

- Hook names are **stage names, not tool names** (`patch` / `configure` /
  `build`, never `autogen`). A future cmake/meson module maps the same three
  stages to its own toolchain and the config reads identically.
- Hooks are the fine-grained escape hatch; the coarse-grained one is the
  `module:` override (swap the whole language module). Reach for `module:`
  only when you need different machinery entirely.

## Matrix builds (contract — machinery pending)

Nothing here is implemented yet: no module does variant fan-out and no
`TRUENORTH_RESULT` payload carries a `variant` field today. This section
documents the target contract. One build check will produce **N build
variants** — e.g. `-march=znver2` for AMD and `-march=x86-64-v2` for Intel. A
matrix is a **fan-out dimension of the build check**: not a new check, not a
separate pipeline. The enforcement model is unchanged — `build` is blocking
and fails if *any* variant fails; the build report aggregates per variant.

```yaml
build:
  enabled: true
  mode: blocking
  module: github.com/snoby-demo-org/dagger-checks-c@v1.1.0
  hooks:                       # shared defaults; a variant may override any slot
    patch:     patches/*.patch
    configure: autogen.sh
    build:     build.sh
  matrix:
    - id: amd-znver2
      env: { MARCH: znver2 }
    - id: intel-x86-64v2
      env: { MARCH: x86-64-v2 }
```

- No `matrix:` → a single implicit variant (exactly today's behavior).
- Variants fan out to parallel runner pods, one job per variant
  (`build (amd-znver2)`); each emits `TRUENORTH_RESULT` with a `variant:`
  field added to the payload.
- Env vars are exported before the hook chain runs, so `build.sh` reads
  `$MARCH` and stays variant-unaware otherwise.
- `truenorth-exceptions.yaml` can target a single variant
  (`check: build`, `variant: intel-x86-64v2`).
- Digest handoff: image-producing variants push `:sha-<variant>`; test-plane
  checks declare which variant they consume (default: the first).

### Target matrix vs platform matrix

| Class | Example | Runner | Cost |
|---|---|---|---|
| **Target matrix** | `-march=znver2` vs `x86-64-v2`, static vs shared | Same container, same runner | One pod per variant, nothing else |
| **Platform matrix** | amd64 vs arm64, glibc vs musl | Variant pins its own `plane:` label; needs real matching runners | Runner provisioning is on you |

"Intel vs AMD" in the instruction-set sense is a **target matrix** — compiler
flags, same hardware. A platform matrix needs matching hardware runners;
that is plane routing, not just flags.

### When is it a different pipeline?

Only when you need different *machinery*: a different language-module family,
a different plane topology, or an unrelated artifact (e.g. firmware + host
app). That is a second repo/component with its own `truenorth.yaml`. A CPU
target, libc variant, or flag set never warrants a second pipeline.

## Known wart and follow-up

The `make` stage runs unconditionally after `build.sh` — so a build script
that fully builds the project (cpuminer-opt-cpupower's does: configure + make
+ packaging) triggers a second, usually no-op `make`. Harmless there, but a
`build.sh` that leaves the tree unmakeable (e.g. it distcleans or the default
target does not exist) would turn a successful build into a false
`make_failed`. Proposed fix (behavior change → `dagger-checks-c` v1.1.1):
when `build.sh` exists, skip the generic make stage — true takeover semantics
matching this document's intent. Pending a decision; until then this page
describes the code as it is.

## Status

| Piece | Status |
|---|---|
| Hook slots (presence detection) | **Shipped** — `dagger-checks-c@v1.1.0` |
| Declared hooks (`hooks:` in truenorth.yaml) | Contract documented; machinery pending |
| Matrix (`matrix:` fan-out, per-variant results/report/digests) | Contract documented; machinery pending (module env passthrough, reusable-workflow fan-out, report aggregation by variant, digest-set handoff) |
| `build.sh` → skip generic make (true takeover) | Proposed v1.1.1; pending decision |
