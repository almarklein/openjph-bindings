from __future__ import annotations

import importlib

import numpy as np
import pytest

openjph_backend = pytest.importorskip("openjph._backend")

RNG = np.random.default_rng(123)


def _make_uint16(shape: tuple[int, ...]) -> np.ndarray:
    return RNG.integers(0, 60_000, size=shape, dtype=np.uint16)


def test_backend_module_importable() -> None:
    mod = importlib.import_module("openjph._backend")
    assert hasattr(mod, "encode")
    assert hasattr(mod, "decode")


def test_public_api_importable() -> None:
    import openjph

    from openjph import _backend

    assert openjph.encode is _backend.encode
    assert openjph.decode is _backend.decode


def test_ctypes_struct_layout_matches_c_abi() -> None:
    # The ctypes structs must match the C structs in native/include/openjph_c.h.
    # Both C structs are naturally aligned with no padding, so ctypes' computed
    # size must equal the sum of field sizes — this catches a field-type drift
    # (e.g. size_t vs int) that would silently corrupt the FFI.
    import ctypes

    from openjph import _backend

    def sum_field_sizes(struct: type[ctypes.Structure]) -> int:
        return sum(ctypes.sizeof(t) for _, t in struct._fields_)

    assert ctypes.sizeof(_backend._Array) == sum_field_sizes(_backend._Array)
    assert ctypes.sizeof(_backend._EncodeParams) == sum_field_sizes(
        _backend._EncodeParams
    )
    # dims[3] in C is mirrored as three contiguous size_t fields dim0/dim1/dim2.
    sz = ctypes.sizeof(ctypes.c_size_t)
    assert _backend._Array.dim1.offset - _backend._Array.dim0.offset == sz
    assert _backend._Array.dim2.offset - _backend._Array.dim1.offset == sz


def test_roundtrip_2d() -> None:
    data = _make_uint16((32, 48))

    encoded = openjph_backend.encode(
        data,
        irreversible=False,
        qstep=None,
        num_decompositions=5,
        block_size=(64, 64),
        progression_order="CPRL",
        color_transform=False,
        planar=True,
    )
    decoded = openjph_backend.decode(encoded)

    np.testing.assert_array_equal(decoded, data)


def test_roundtrip_3d() -> None:
    data = _make_uint16((4, 24, 32))

    encoded = openjph_backend.encode(
        data,
        irreversible=False,
        qstep=None,
        num_decompositions=5,
        block_size=(64, 64),
        progression_order="CPRL",
        color_transform=False,
        planar=True,
    )
    decoded = openjph_backend.decode(encoded)

    np.testing.assert_array_equal(decoded, data)


def test_singleton_component_decodes_as_2d() -> None:
    # A (1, h, w) array encodes to a 1-component codestream whose SIZ marker is
    # indistinguishable from (h, w), so the low-level decode returns 2-D. This
    # ambiguity is inherent to the codestream; callers with a target shape
    # (e.g. the Zarr codecs) are responsible for restoring the singleton axis.
    data = _make_uint16((1, 24, 32))
    decoded = openjph_backend.decode(openjph_backend.encode(data))
    assert decoded.shape == (24, 32)
    np.testing.assert_array_equal(decoded, data[0])


def test_roundtrip_3d_stack_distinct_slices() -> None:
    # A 3-D array whose leading dimension is a stack of *independent* slices
    # (a volumetric stack, NOT a color transform) must round-trip each slice
    # exactly into a single codestream.
    Z, Y, X = 8, 24, 32
    data = np.stack(
        [
            np.full((Y, X), s * 1000, np.uint16)
            + (np.arange(Y * X, dtype=np.uint16).reshape(Y, X) % 500)
            for s in range(Z)
        ]
    )
    decoded = openjph_backend.decode(openjph_backend.encode(data, planar=True))

    assert decoded.shape == data.shape
    for s in range(Z):
        np.testing.assert_array_equal(decoded[s], data[s])
    assert len({decoded[s].tobytes() for s in range(Z)}) == Z  # slices distinct


def test_error_message_carries_openjph_detail() -> None:
    # OpenJPH-internal failures must surface the library's detailed diagnostic
    # (message text, source location) in the raised exception. OpenJPH's
    # default handler prints that detail to stderr and throws a generic
    # "ojph error" — the wrapper installs a capturing handler instead.
    bad = b"\xff\x4f" + b"\x00" * 62  # SOC marker followed by a garbage SIZ
    with pytest.raises(RuntimeError, match="SIZ") as excinfo:
        openjph_backend.decode(bad)
    assert "ojph error" not in str(excinfo.value)


def test_irreversible_uint16_roundtrip() -> None:
    data = _make_uint16((32, 48))

    encoded = openjph_backend.encode(
        data,
        irreversible=True,
        qstep=0.01,
        num_decompositions=5,
        block_size=(64, 64),
        progression_order="CPRL",
        color_transform=False,
        planar=True,
    )
    decoded = openjph_backend.decode(encoded)

    assert decoded.shape == data.shape
    assert decoded.dtype == np.uint16
    assert not np.array_equal(decoded, data)
    diff = np.abs(decoded.astype(np.int64) - data.astype(np.int64))
    assert diff.mean() < 250
    assert diff.max() < 1000
