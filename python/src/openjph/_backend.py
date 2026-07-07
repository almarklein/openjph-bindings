from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path

import numpy as np

from openjph._constants import PROGRESSION_ORDERS

# Resolve platform-specific library filename.
_platform = sys.platform
if _platform == "win32":
    _lib_name = "openjph_c.dll"
elif _platform == "darwin":
    _lib_name = "libopenjph_c.dylib"
else:
    _lib_name = "libopenjph_c.so"

_pkg_dir = Path(__file__).parent
_lib_path = _pkg_dir / _lib_name

# On Windows, ctypes searches PATH but not the package directory for transitive DLLs.
if _platform == "win32" and hasattr(os, "add_dll_directory"):
    os.add_dll_directory(str(_pkg_dir))

try:
    _lib = ctypes.CDLL(str(_lib_path) if _lib_path.exists() else _lib_name)
except OSError as e:
    raise ImportError(f"Could not load {_lib_name}: {e}") from e

# ---- C struct mirrors ----


class _Array(ctypes.Structure):
    _fields_ = [
        ("data", ctypes.c_void_p),
        ("ndim", ctypes.c_size_t),
        ("dim0", ctypes.c_size_t),  # dims[0]
        ("dim1", ctypes.c_size_t),  # dims[1]
        ("dim2", ctypes.c_size_t),  # dims[2]
        ("bit_depth", ctypes.c_uint32),
        ("is_signed", ctypes.c_int32),
    ]


class _EncodeParams(ctypes.Structure):
    _fields_ = [
        ("irreversible", ctypes.c_int),
        ("qstep", ctypes.c_float),
        ("use_qstep", ctypes.c_int),
        ("num_decompositions", ctypes.c_int),
        ("block_width", ctypes.c_int),
        ("block_height", ctypes.c_int),
        ("progression_order", ctypes.c_char * 8),
        ("color_transform", ctypes.c_int),
        ("planar", ctypes.c_int),
    ]


# ---- dtype lookup tables ----

_DTYPE_TO_BD_SIGNED: dict[np.dtype, tuple[int, int]] = {
    np.dtype("uint8"): (8, 0),
    np.dtype("int8"): (8, 1),
    np.dtype("uint16"): (16, 0),
    np.dtype("int16"): (16, 1),
    np.dtype("uint32"): (32, 0),
    np.dtype("int32"): (32, 1),
}

_BD_SIGNED_TO_DTYPE: dict[tuple[int, int], np.dtype] = {
    v: k for k, v in _DTYPE_TO_BD_SIGNED.items()
}

# ---- configure function signatures ----

_lib.openjph_encode.restype = ctypes.c_int
_lib.openjph_encode.argtypes = [
    ctypes.POINTER(_Array),
    ctypes.POINTER(_EncodeParams),
    ctypes.POINTER(ctypes.c_void_p),
    ctypes.POINTER(ctypes.c_size_t),
    ctypes.c_char_p,
    ctypes.c_size_t,
]

_lib.openjph_decode.restype = ctypes.c_int
_lib.openjph_decode.argtypes = [
    ctypes.c_char_p,
    ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_void_p),
    ctypes.POINTER(ctypes.c_size_t),
    ctypes.POINTER(ctypes.c_size_t),
    ctypes.POINTER(ctypes.c_size_t),  # out_dims[3] decays to size_t*
    ctypes.POINTER(ctypes.c_uint32),
    ctypes.POINTER(ctypes.c_int32),
    ctypes.c_char_p,
    ctypes.c_size_t,
]

_lib.openjph_free.restype = None
_lib.openjph_free.argtypes = [ctypes.c_void_p]


# ---- public API ----


def encode(
    array: np.ndarray,
    *,
    irreversible: bool = False,
    qstep: float | None = None,
    num_decompositions: int = 5,
    block_size: tuple[int, int] = (64, 64),
    progression_order: str = "LRCP",
    color_transform: bool = False,
    planar: bool = True,
) -> bytes:
    if progression_order not in PROGRESSION_ORDERS:
        raise ValueError(
            f"Unsupported progression_order {progression_order!r}; "
            f"expected one of {list(PROGRESSION_ORDERS)}"
        )
    if num_decompositions < 0:
        raise ValueError(f"num_decompositions must be >= 0, got {num_decompositions}")
    if block_size[0] <= 0 or block_size[1] <= 0:
        raise ValueError(f"block_size values must be positive, got {block_size!r}")

    arr = np.ascontiguousarray(array)
    bd_sgn = _DTYPE_TO_BD_SIGNED.get(arr.dtype)
    if bd_sgn is None:
        raise ValueError(
            f"Unsupported dtype {arr.dtype}; expected one of {list(_DTYPE_TO_BD_SIGNED)}"
        )
    bit_depth, is_signed_val = bd_sgn

    ndim = arr.ndim
    if ndim == 2:
        d0, d1, d2 = arr.shape[0], arr.shape[1], 0
    elif ndim == 3:
        d0, d1, d2 = arr.shape[0], arr.shape[1], arr.shape[2]
    else:
        raise ValueError(f"array must be 2-D or 3-D, got {ndim}-D")

    img = _Array(
        data=arr.ctypes.data,
        ndim=ndim,
        dim0=d0,
        dim1=d1,
        dim2=d2,
        bit_depth=bit_depth,
        is_signed=is_signed_val,
    )
    params = _EncodeParams(
        irreversible=int(irreversible),
        qstep=float(qstep) if qstep is not None else 0.0,
        use_qstep=int(qstep is not None),
        num_decompositions=num_decompositions,
        block_width=block_size[0],
        block_height=block_size[1],
        progression_order=progression_order.encode("ascii")[:8],
        color_transform=int(color_transform),
        planar=int(planar),
    )

    out_ptr = ctypes.c_void_p(0)
    out_len = ctypes.c_size_t(0)
    err_buf = ctypes.create_string_buffer(1024)

    ret = _lib.openjph_encode(
        ctypes.byref(img),
        ctypes.byref(params),
        ctypes.byref(out_ptr),
        ctypes.byref(out_len),
        err_buf,
        ctypes.c_size_t(1024),
    )
    if ret != 0:
        raise RuntimeError(f"openjph_encode: {err_buf.value.decode(errors='replace')}")

    result = bytes(ctypes.string_at(out_ptr.value, int(out_len.value)))
    _lib.openjph_free(out_ptr)
    return result


def decode(data: bytes | np.ndarray) -> np.ndarray:
    cs = bytes(data) if not isinstance(data, bytes) else data

    out_ptr = ctypes.c_void_p(0)
    out_len = ctypes.c_size_t(0)
    out_ndim = ctypes.c_size_t(0)
    out_dims = (ctypes.c_size_t * 3)(0, 0, 0)
    out_bit_depth = ctypes.c_uint32(0)
    out_is_signed = ctypes.c_int32(0)
    err_buf = ctypes.create_string_buffer(1024)

    ret = _lib.openjph_decode(
        cs,
        ctypes.c_size_t(len(cs)),
        ctypes.byref(out_ptr),
        ctypes.byref(out_len),
        ctypes.byref(out_ndim),
        out_dims,
        ctypes.byref(out_bit_depth),
        ctypes.byref(out_is_signed),
        err_buf,
        ctypes.c_size_t(1024),
    )
    if ret != 0:
        raise RuntimeError(f"openjph_decode: {err_buf.value.decode(errors='replace')}")

    key = (int(out_bit_depth.value), int(out_is_signed.value))
    dtype = _BD_SIGNED_TO_DTYPE.get(key)
    if dtype is None:
        raise RuntimeError(
            f"openjph_decode: unknown output type bit_depth={key[0]}, is_signed={key[1]}"
        )

    ndim = int(out_ndim.value)
    shape = tuple(int(out_dims[i]) for i in range(ndim))

    raw = bytes(ctypes.string_at(out_ptr.value, int(out_len.value)))
    _lib.openjph_free(out_ptr)
    return np.frombuffer(raw, dtype=dtype).reshape(shape)
