# Releasing & binary distribution

This repo ships its native library (`libopenjph_c`) to two ecosystems:

- **Python** → PyPI wheels (built by `wheels.yml`, published on `v*` tags). Each wheel bundles the
  native lib; users get it via `pip install pyopenjph`. Nothing for Python goes to GitHub Releases.
- **Julia** → per-platform binaries published as GitHub **Release tarballs** by the
  `publish-native` job in `wheels.yml`, bound by `julia/OpenJPH.jl/Artifacts.toml` and dispatched by
  Pkg. These are **recycled from the wheels** (`tools/extract_native_from_wheels.sh` pulls the lib
  cibuildwheel already built), not a separate build — valid because OpenJPH is statically embedded.

## Current state (branch `ds-init`, bound to a frozen snapshot)

The consumer switch IS applied, bound to the immutable **`binaries-snapshot`** release:

- `OpenJPH.jl` loads the native lib from a **local build override** if one exists (produced by
  `deps/build.jl` from `NATIVE_PATH` or the in-monorepo `native/`), otherwise from
  `artifact"libopenjph_c"` — `Artifacts.toml` binds all five platforms to the `binaries-snapshot`
  tarballs.
- Monorepo development still uses the local build (no artifact download); only a **standalone
  consumer** (e.g. a downstream package pulling OpenJPH from GitHub) uses the artifact.

**Why a snapshot:** `binaries-snapshot` is a GitHub release on a **non-`v` tag**, so it triggers no
CI and is never rebuilt — its assets' `sha256` are constant, so `Artifacts.toml` stays valid no
matter how much `ds-init` is pushed. (The PR's own `dev-pr-3` pre-release *does* rebuild on every
push and must NOT be used for a committed `Artifacts.toml`.)

**To refresh the snapshot** (only when you intentionally want downstream to pick up new binaries —
e.g. after a real change to the C layer), rebuild via a PR push, then:
```bash
# copy the latest dev-pr-3 tarballs into the snapshot (drop the -dev suffix), then:
gh release upload binaries-snapshot libopenjph_c-*-*.tar.gz --clobber
julia --project=@artifactgen tools/gen_artifacts.jl binaries-snapshot   # needs ArtifactUtils
# commit the updated julia/OpenJPH.jl/Artifacts.toml
```
When you cut a real `v*` tag, regenerate against it (`gen_artifacts.jl v0.1.0`) and delete the
snapshot.

### Consuming these from a downstream package (on the branch)

`[sources]` is only honoured for the **top-level** project, not for a dependency — so a downstream
package must declare **both** modules in its own `Project.toml`:
```toml
[sources]
OpenJPH          = {url = "https://github.com/Clepio-Biotech/openjph-bindings", subdir = "julia/OpenJPH.jl",          rev = "ds-init"}
ZarrCompressorJPH = {url = "https://github.com/Clepio-Biotech/openjph-bindings", subdir = "julia/ZarrCompressorJPH.jl", rev = "ds-init"}
```
The downstream then resolves both from GitHub; OpenJPH supplies its binary via `Artifacts.toml`
(the `dev-pr-3` artifact). This requires the OpenJPH artifact-loading code + `Artifacts.toml` to be
**pushed to `ds-init`**.

## Activating Artifacts-based distribution (the D6 consumer switch)

Do this once `release.yml` has published the per-platform tarballs for a real tag:

1. **Cut a tag** `vX.Y.Z` and push it. `wheels.yml` builds the wheels, publishes them to PyPI, and
   its `publish-native` job extracts the bundled native lib from each wheel and attaches
   `libopenjph_c-<platform>.tar.gz` for `linux-{x86_64,aarch64}`, `macos-{x86_64,arm64}`,
   `windows-x86_64` to the GitHub release.
2. **Generate `Artifacts.toml`:**
   ```bash
   julia -e 'import Pkg; Pkg.activate(temp=true); Pkg.add("ArtifactUtils")' \
         tools/gen_artifacts.jl vX.Y.Z
   ```
   This writes `julia/OpenJPH.jl/Artifacts.toml` with a lazy, platform-dispatched binding for each
   tarball (url + sha256 + tree-hash). Commit it.
3. **Switch `OpenJPH.jl` to load from the artifact** instead of the build product:
   ```julia
   using Pkg.Artifacts
   const libopenjph_c = joinpath(artifact"libopenjph_c", "libopenjph_c." * _dlext)
   ```
   and reduce `deps/build.jl` to the `NATIVE_PATH` dev override only (Pkg now supplies the binary
   per platform; no download/regex logic).
4. **Switch `ZarrCompressorJPH`'s `[sources]`** from the path form to the URL form so a standalone
   `Pkg.add(url=..., subdir="julia/ZarrCompressorJPH.jl")` resolves:
   ```toml
   [sources]
   OpenJPH = {url = "https://github.com/Clepio-Biotech/openjph-bindings", subdir = "julia/OpenJPH.jl", rev = "vX.Y.Z"}
   ```
5. **Keep versions in sync** — bump `pyproject.toml`, both `Project.toml`, and the download-tag
   constants together; `tests/check_versions.sh` (run in CI) enforces this.

## Notes / pitfalls

- The native libs are **recycled from the wheels**, so cibuildwheel's platform handling carries
  over for free: Linux libs are manylinux (glibc floor matches the wheels), `aarch64` via QEMU,
  macOS from the x86_64/arm64 runners, Windows as the runtime DLL.
- Because OpenJPH is statically embedded (`WHOLE_ARCHIVE`), the in-wheel lib has **no external
  deps**, so `auditwheel`/`delocate` do not vendor or rename it — extracting it for Julia is safe.
- The extractor (`tools/extract_native_from_wheels.sh`) renames the Windows lib (`openjph_c.dll`) to
  the canonical `libopenjph_c.dll` inside the tarball; Linux/macOS already ship
  `libopenjph_c.{so,dylib}`. It skips musllinux wheels and de-dups across cp312/cp313.
