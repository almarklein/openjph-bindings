# pyopenjph

Python bindings for OpenJPH HTJ2K encode/decode, with an optional Zarr v3 codec.

## Installation

```bash
pip install "pyopenjph[zarr]"   # with Zarr codec support
pip install pyopenjph           # encode/decode only
```

## Basic usage

```python
import openjph
import numpy as np

data = np.random.randint(0, 60000, (64, 128), dtype=np.uint16)

encoded = openjph.encode(
    data,
    irreversible=False,
    qstep=None,
    num_decompositions=5,
    block_size=(64, 64),
    progression_order="LRCP",
    color_transform=False,
    planar=True,
)
decoded = openjph.decode(encoded)

np.testing.assert_array_equal(decoded, data)
```

`decode` requires no dtype or shape hint from the caller. HTJ2K codestreams carry a SIZ
(image size) marker in their header that records the image dimensions, number of
components, bit depth, and signedness at encode time. `decode` reads this marker and
uses it to allocate the output buffer and select the correct element type, so the
reconstructed array always matches the original without any extra bookkeeping on the
caller's side.

## Zarr v3 codec

`OpenJPHCodec` is a Zarr v3 array-to-bytes codec passed in the array's `codecs` pipeline.

```python
from openjph.zarr import OpenJPHCodec
import numpy as np
import zarr

data = np.arange(64 * 96, dtype=np.uint16).reshape(64, 96)

array = zarr.create(
    store="example.zarr",
    shape=data.shape,
    chunks=data.shape,
    dtype=data.dtype,
    codecs=[OpenJPHCodec(layout="yx")],
    zarr_format=3,
)

array[:] = data
np.testing.assert_array_equal(array[:], data)
```
