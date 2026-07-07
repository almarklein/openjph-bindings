# openjph-bindings

Language bindings for [OpenJPH](https://github.com/aous72/OpenJPH) — a high-performance HTJ2K (High Throughput JPEG 2000) codec.

## Repository layout

```
openjph-bindings/
├── native/     # C ABI shared library (libopenjph_c.so) — the single source of truth
├── python/     # pyopenjph — Python bindings and optional Zarr v3 codec
└── julia/      # OpenJPH.jl and ZarrCompressorJPH.jl — Julia bindings and Zarr codec
```

The design principle is that `native/` is the only place where C++ touches OpenJPH. Both language wrappers depend on it and have no independent C++ build paths.

---

## How `libopenjph_c` reaches each language

The native library is **distributed as a prebuilt binary** to both ecosystems; building from
source is only needed when developing the C layer itself.

| | Julia | Python |
|---|---|---|
| **Prebuilt (default)** | Pkg downloads the right per-platform binary via `OpenJPH.jl/Artifacts.toml` from a GitHub Release — no C++ toolchain needed | `pip install pyopenjph` installs a wheel that already contains the binary — cmake never runs |
| **Local build (override)** | set `NATIVE_PATH`, or build inside the monorepo (the sibling `native/` is auto-detected); `build.jl` runs cmake and the local build **takes precedence over the artifact** | scikit-build-core runs cmake on `python/CMakeLists.txt`, which finds `native/` via `NATIVE_PATH` → sibling → FetchContent |

So a normal install needs no compiler on either side. Developers who change the C layer get a local
build by setting `NATIVE_PATH` (or, in the monorepo, automatically from the sibling `native/`),
which overrides the prebuilt binary. See `docs/RELEASING.md` for how the Julia binaries are built
(recycled from the Python wheels) and published as artifacts.

### Setting `NATIVE_PATH`

A local build (overriding the prebuilt binary) is triggered by setting `NATIVE_PATH` to the path of a local `native/` directory. This can be done via:

- A shell environment variable: `NATIVE_PATH=/path/to/native`
- A `.env` file in the package root (`julia/OpenJPH.jl/.env` or `python/.env`):
  ```
  NATIVE_PATH=/path/to/openjph-bindings/native
  ```

Inside this monorepo `NATIVE_PATH` is **optional**: the Julia `build.jl` and the Python
`CMakeLists.txt` both auto-detect the sibling `native/` directory, so a fresh monorepo build works
with no environment variable. Set `NATIVE_PATH` only to point at a `native/` directory elsewhere.

---

## Developer setup

### Prerequisites

- cmake ≥ 3.24 and a C++17 compiler (only for local/source builds)
- Julia ≥ 1.11 (for `[sources]` and Pkg Artifacts)
- Python ≥ 3.12

### Julia

```bash
# Build from the sibling native/ (auto-detected in the monorepo — no NATIVE_PATH needed)
julia --project=julia/OpenJPH.jl -e 'import Pkg; Pkg.build("OpenJPH")'

# Run tests (Pkg.test with no argument tests the active project's package)
julia --project=julia/OpenJPH.jl           -e 'import Pkg; Pkg.test()'
julia --project=julia/ZarrCompressorJPH.jl -e 'import Pkg; Pkg.test()'
```

### Python

```bash
# Build from the local native/ (auto-detected in the monorepo). A non-editable
# install is used so the compiled libopenjph_c is placed next to the package; a
# scikit-build-core editable install leaves it in a build dir the loader can't find.
pip install "python/[test,zarr]"

# Run tests
cd python && pytest tests/
```

### native (standalone)

`native/` can be built and tested independently of both language wrappers:

```bash
cmake -B build native/ -DCMAKE_BUILD_TYPE=Release
cmake --build build -j$(nproc)
# Produces: build/libopenjph_c.so
```
