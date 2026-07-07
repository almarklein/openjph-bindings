"""Cross-language Zarr interop (Python writer).

Writes the same uint16 array twice: once with a plain (default) codec and once
with OpenJPHCodec. ``jl_read.jl`` then reads both in Julia and asserts they are
identical. Comparing the HTJ2K store against the plain store isolates *codec*
correctness from Zarr.jl's axis-order convention (Zarr.jl reverses dimension
order relative to python-zarr), so this test does not depend on that convention.
"""

from __future__ import annotations

import shutil
import sys

import numpy as np
import zarr

from openjph.zarr import OpenJPHCodec


def main(outdir: str) -> None:
    H, W = 64, 96
    data = (np.arange(H * W, dtype=np.uint16).reshape(H, W)) % 60000
    for name, codecs in (
        ("plain", None),
        ("htj2k", [OpenJPHCodec(layout="yx", block_size=(32, 64))]),
    ):
        store = f"{outdir}/py_{name}.zarr"
        shutil.rmtree(store, ignore_errors=True)
        kw: dict = dict(store=store, shape=(H, W), chunks=(H, W), dtype="uint16")
        if codecs is not None:
            kw["codecs"] = codecs
        arr = zarr.create(**kw)
        arr[:] = data
        assert np.array_equal(arr[:], data), f"{name}: python round-trip not exact"

    # 3-D stores with singleton-component chunks: each (1, H, W) chunk encodes
    # to a 1-component codestream whose SIZ marker cannot express the leading
    # singleton axis — the reader must restore it (PR #3 regression).
    Z = 4
    data3d = (np.arange(Z * H * W, dtype=np.uint16).reshape(Z, H, W)) % 60000
    for name, codecs in (
        ("plain3d", None),
        ("htj2k3d", [OpenJPHCodec(layout="zyx")]),
    ):
        store = f"{outdir}/py_{name}.zarr"
        shutil.rmtree(store, ignore_errors=True)
        kw = dict(store=store, shape=(Z, H, W), chunks=(1, H, W), dtype="uint16")
        if codecs is not None:
            kw["codecs"] = codecs
        arr = zarr.create(**kw)
        arr[:] = data3d
        assert np.array_equal(arr[:], data3d), f"{name}: python round-trip not exact"

    print("python wrote py_plain.zarr, py_htj2k.zarr, py_plain3d.zarr, py_htj2k3d.zarr")


if __name__ == "__main__":
    main(sys.argv[1])
