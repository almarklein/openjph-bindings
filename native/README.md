# native

A thin C ABI wrapper around [OpenJPH](https://github.com/aous72/OpenJPH), producing
a self-contained shared library (`libopenjph_c.so`) that both the Python and Julia
packages depend on.

OpenJPH is embedded statically into the shared library at build time via
`--whole-archive`, so there is no runtime dependency on a separate OpenJPH installation.

## Build

```bash
cmake -B build . -DCMAKE_BUILD_TYPE=Release
cmake --build build -j$(nproc)
# Produces: build/libopenjph_c.so
```

Requires cmake ≥ 3.24 (for `LINK_LIBRARY:WHOLE_ARCHIVE`). CMake downloads OpenJPH automatically
via FetchContent — no prior installation needed.

## API

Three exported symbols:

- `openjph_encode` — compress an array to an HTJ2K codestream
- `openjph_decode` — decompress a codestream; element type and shape are read from the SIZ marker
- `openjph_free` — free a buffer returned by encode or decode

See `include/openjph_c.h` for the full C interface.
