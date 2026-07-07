# julia

Julia bindings for OpenJPH HTJ2K encode/decode, built on top of `libopenjph_c.so`
from the `native/` package.

Two packages live here:

- **`OpenJPH.jl`** — low-level Julia wrapper around the C ABI; exposes `openjph_encode`
  and `openjph_decode` for all supported integer types (UInt8, Int8, UInt16, Int16,
  UInt32, Int32).
- **`ZarrCompressorJPH.jl`** — Zarr v3 array-to-bytes codec (`HTJ2KCodec`) built on
  top of `OpenJPH.jl`.

## Installation

These packages are unregistered, so declare them via `[sources]` in your project's
`Project.toml` (Julia ≥ 1.11). `ZarrCompressorJPH` depends on `OpenJPH`, and `[sources]`
is only honoured for the top-level project, so list **both**:

```toml
[sources]
OpenJPH           = {url = "https://github.com/Clepio-Biotech/openjph-bindings", subdir = "julia/OpenJPH.jl"}
ZarrCompressorJPH = {url = "https://github.com/Clepio-Biotech/openjph-bindings", subdir = "julia/ZarrCompressorJPH.jl"}
```

```julia
using Pkg
Pkg.add(["OpenJPH", "ZarrCompressorJPH"])
```

`OpenJPH` supplies its native binary automatically from `Artifacts.toml` (a per-platform
prebuilt download) — no C++ toolchain needed. Add `rev = "<tag>"` to pin a release; see
`docs/RELEASING.md` for installing from a development branch.

## Basic usage

```julia
using OpenJPH

data = rand(UInt16, 64, 128)
encoded = openjph_encode(data)
decoded = openjph_decode(encoded)
@assert decoded == data
```

## Development

```bash
# In the monorepo the sibling native/ is auto-detected (no NATIVE_PATH needed); a
# local build overrides the artifact so you can test changes to the C layer.
julia --project=OpenJPH.jl -e 'import Pkg; Pkg.build("OpenJPH")'

# Pkg.test with no argument tests the active project's package.
julia --project=OpenJPH.jl           -e 'import Pkg; Pkg.test()'
julia --project=ZarrCompressorJPH.jl -e 'import Pkg; Pkg.test()'
```
