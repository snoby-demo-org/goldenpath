# TrueNorth build contract: hooks & matrix

How a repo controls the **build leg** of the TrueNorth pipeline. The machinery
lives once — pinned reusable workflow (`goldenpath/truenorth.yml@vN`) plus the
language Dagger module (`dagger-checks-c@v1.1.0` for C/C++). What a repo owns
is its **knowledge**: `.github/truenorth.yaml` and the hook files described
here (Option B: machinery pinned by tag, recipes owned by the repo).

## The build contract

The C/C++ build leg executes three stages, in order, inside a pinned
`ubuntu:24.04` toolchain container (build-essential, autoconf, libcurl,
libjansson, openssl, libgmp, libhwloc, ccache):

```
patch → configure → build(make)
```

Each stage has a hook slot. **Hooks are files in the repo root** — drop them
in and the pipeline uses them; no config required.

### Hook slots

| Stage | Hook (presence-detected) | Behavior | Failure status |
|---|---|---|---|
| patch | `patches/*.patch` | Applied in glob order with `patch -p1` before anything else | `patch_failed` |
| configure | `autogen.sh` / `./configure` | `autogen.sh` runs first if present (a fresh clone's shipped `configure` can be stale vs `Makefile.am`); else `./configure` | `autogen_failed`, `configure_failed`, `no_configure` |
| build | `build.sh` | If present it **owns the whole build** (its own configure + make); the generic path does not run. Otherwise generic `make -j"$(nproc)"` | `build_failed`, `make_failed` |

Rules:

- **Presence detection is the default.** No `truenorth.yaml` change needed to
  use a hook.
- If `build.sh` exists, the check never runs its own configure/make —
  the script is fully responsible. (`cpuminer-opt-cpupower` builds this way:
  znver2 flags, static libstdc++, versioned HiveOS packaging.)
- The check **fails** on any stage failure or when no ELF binary
  (`cpuminer*` / `minerd*`) is produced. The failing stage's last 60 log
  lines are surfaced in the error.
- Compiler warnings are counted and reported; they do **not** fail the check.
- Result contract (FR-7):

  ```
  TRUENORTH_RESULT {"check":"build","tool":"autotools@ubuntu:24.04",
                    "status":"pass|fail","failed_stage":...,"warnings":N,
                    "binary":"…"}
  ```

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
    build:     build.sh          # full takeover; omit → ./configure && make
```

- Hook names are **stage names, not tool names** (`patch` / `configure` /
  `build`, never `autogen`). A future cmake/meson module maps the same three
  stages to its own toolchain and the config reads identically.
- Hooks are the fine-grained escape hatch; the coarse-grained one is the
  `module:` override (swap the whole language module). Reach for `module:`
  only when you need different machinery entirely.

## Matrix builds

One build check can produce **N build variants** — e.g. `-march=znver2` for
AMD and `-march=x86-64-v2` for Intel. A matrix is a **fan-out dimension of
the build check**: not a new check, not a separate pipeline. The enforcement
model is unchanged — `build` is blocking and fails if *any* variant fails;
the build report aggregates per variant.

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

## Status

| Piece | Status |
|---|---|
| Hook slots (presence detection) | **Shipped** — `dagger-checks-c@v1.1.0` |
| Declared hooks (`hooks:` in truenorth.yaml) | Contract documented; machinery pending |
| Matrix (`matrix:` fan-out, per-variant results/report/digests) | Contract documented; machinery pending (module env passthrough, reusable-workflow fan-out, report aggregation by variant, digest-set handoff) |
