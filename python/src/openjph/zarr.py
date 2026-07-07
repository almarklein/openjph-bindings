from __future__ import annotations

import asyncio
import importlib
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal, Protocol, cast

import numpy as np

from zarr.abc.codec import ArrayBytesCodec
from zarr.core.common import JSON, parse_named_configuration

from openjph._constants import PROGRESSION_ORDERS

if TYPE_CHECKING:
    from typing import Self

    from zarr.core.array_spec import ArraySpec
    from zarr.core.buffer import Buffer, NDBuffer
    from zarr.core.chunk_grids import ChunkGrid
    from zarr.core.dtype.wrapper import TBaseDType, TBaseScalar, ZDType


Layout = Literal["yx", "zyx", "cyx", "yxc"]
ProgressionOrder = Literal["LRCP", "RLCP", "RPCL", "PCRL", "CPRL"]
_SUPPORTED_PROGRESSIONS = set(PROGRESSION_ORDERS)
_BACKEND_MODULE_NAME = "openjph._backend"


class OpenJPHCodecUnavailableError(RuntimeError):
    pass


class _OpenJPHBackend(Protocol):
    def encode(
        self,
        array: np.ndarray,
        *,
        irreversible: bool,
        qstep: float | None,
        num_decompositions: int,
        block_size: tuple[int, int],
        progression_order: str,
        color_transform: bool,
        planar: bool,
    ) -> bytes: ...

    def decode(self, data: bytes) -> np.ndarray: ...


_backend_cache: _OpenJPHBackend | None = None
_backend_attempted = False


def _get_backend() -> _OpenJPHBackend:
    global _backend_cache, _backend_attempted

    if _backend_cache is not None:
        return _backend_cache

    if not _backend_attempted:
        _backend_attempted = True
        try:
            module = importlib.import_module(_BACKEND_MODULE_NAME)
        except ImportError:
            module = None
        if module is not None:
            _backend_cache = cast("_OpenJPHBackend", module)
            return _backend_cache

    raise OpenJPHCodecUnavailableError(
        "OpenJPHCodec requires the native openjph._backend module."
    )


def _normalize_layout(layout: Layout | str | None) -> Layout | None:
    if layout is None:
        return None
    normalized = layout.lower()
    if normalized not in {"yx", "zyx", "cyx", "yxc"}:
        raise ValueError(f"Unsupported OpenJPH layout: {layout!r}")
    return cast("Layout", normalized)


def _normalize_progression_order(name: str) -> str:
    normalized = name.upper()
    if normalized not in _SUPPORTED_PROGRESSIONS:
        raise ValueError(
            f"Unsupported progression order {name!r}; "
            f"expected one of {sorted(_SUPPORTED_PROGRESSIONS)}"
        )
    return normalized


def _normalize_xy_pair(name: str, value: tuple[int, int]) -> tuple[int, int]:
    if len(value) != 2:
        raise ValueError(f"{name} must be a pair of integers, got {value!r}")
    x, y = int(value[0]), int(value[1])
    if x <= 0 or y <= 0:
        raise ValueError(f"{name} values must be positive, got {value!r}")
    return (x, y)


def _default_layout(shape: tuple[int, ...]) -> Layout:
    if len(shape) == 2:
        return "yx"
    if len(shape) == 3:
        return "zyx"
    raise ValueError(
        f"OpenJPHCodec only supports 2-D or 3-D chunks, got shape {shape!r}"
    )


def _component_count(shape: tuple[int, ...], layout: Layout) -> int:
    if layout == "yx":
        return 1
    if layout in {"zyx", "cyx"}:
        return int(shape[0])
    return int(shape[-1])


def _backend_shape(shape: tuple[int, ...], layout: Layout) -> tuple[int, ...]:
    if layout == "yx":
        return shape
    if layout in {"zyx", "cyx"}:
        return shape
    c = shape[-1]
    y, x = shape[0], shape[1]
    return (c, y, x)


def _normalize_for_backend(data: np.ndarray, layout: Layout) -> np.ndarray:
    if layout == "yxc":
        data = np.moveaxis(data, -1, 0)
    return np.ascontiguousarray(data)


def _denormalize_from_backend(data: np.ndarray, layout: Layout) -> np.ndarray:
    if layout == "yxc":
        data = np.moveaxis(data, 0, -1)
    return np.ascontiguousarray(data)


def _supported_native_dtype(native: np.dtype) -> bool:
    # The codec deliberately restricts to uint8/uint16/int16 — the integer types
    # that are well-behaved through HTJ2K for imaging data. The low-level backend
    # additionally accepts int8/uint32/int32, but those are not exposed here.
    if np.issubdtype(native, np.integer):
        return native in {np.dtype("uint8"), np.dtype("uint16"), np.dtype("int16")}
    return False


@dataclass(frozen=True)
class OpenJPHCodec(ArrayBytesCodec):
    is_fixed_size = False

    layout: Layout | None
    irreversible: bool | None
    qstep: float | None
    num_decompositions: int | None
    block_size: tuple[int, int]
    progression_order: str
    color_transform: bool | None
    planar: bool | None

    def __init__(
        self,
        *,
        layout: Layout | str | None = None,
        irreversible: bool | None = None,
        qstep: float | None = None,
        num_decompositions: int | None = None,
        block_size: tuple[int, int] = (64, 64),
        progression_order: ProgressionOrder | str = "LRCP",
        color_transform: bool | None = None,
        planar: bool | None = None,
    ) -> None:
        normalized_layout = _normalize_layout(layout)
        normalized_block_size = _normalize_xy_pair("block_size", block_size)
        normalized_progression = _normalize_progression_order(progression_order)

        if qstep is not None and qstep <= 0:
            raise ValueError(f"qstep must be positive, got {qstep}")
        if num_decompositions is not None and num_decompositions < 0:
            raise ValueError(
                f"num_decompositions must be >= 0, got {num_decompositions}"
            )
        # Fail eagerly for any non-irreversible setting (including the default
        # None, which evolve_from_array_spec resolves to False), so a bare codec
        # object raises at construction rather than only at array-creation time.
        if qstep is not None and irreversible is not True:
            raise ValueError("qstep is only valid for irreversible encoding")

        object.__setattr__(self, "layout", normalized_layout)
        object.__setattr__(self, "irreversible", irreversible)
        object.__setattr__(self, "qstep", qstep)
        object.__setattr__(self, "num_decompositions", num_decompositions)
        object.__setattr__(self, "block_size", normalized_block_size)
        object.__setattr__(self, "progression_order", normalized_progression)
        object.__setattr__(self, "color_transform", color_transform)
        object.__setattr__(self, "planar", planar)

    @classmethod
    def from_dict(cls, data: dict[str, JSON]) -> Self:
        _, configuration_parsed = parse_named_configuration(
            data, "openjph_htj2k", require_configuration=False
        )
        configuration_parsed = configuration_parsed or {}
        if "block_size" in configuration_parsed:
            block_size = configuration_parsed["block_size"]
            if isinstance(block_size, list):
                configuration_parsed["block_size"] = tuple(block_size)
        return cls(**configuration_parsed)  # type: ignore[arg-type]

    def to_dict(self) -> dict[str, JSON]:
        conf: dict[str, JSON] = {
            "block_size": [self.block_size[0], self.block_size[1]],
            "progression_order": self.progression_order,
        }
        if self.layout is not None:
            conf["layout"] = self.layout
        if self.irreversible is not None:
            conf["irreversible"] = self.irreversible
        if self.qstep is not None:
            conf["qstep"] = self.qstep
        if self.num_decompositions is not None:
            conf["num_decompositions"] = self.num_decompositions
        if self.color_transform is not None:
            conf["color_transform"] = self.color_transform
        if self.planar is not None:
            conf["planar"] = self.planar
        return {"name": "openjph_htj2k", "configuration": conf}

    def evolve_from_array_spec(self, array_spec: ArraySpec) -> Self:
        updates: dict[str, object] = {}
        layout = self.layout or _default_layout(array_spec.shape)

        if self.layout is None:
            updates["layout"] = layout
        if self.irreversible is None:
            updates["irreversible"] = False
        if self.num_decompositions is None:
            updates["num_decompositions"] = 5
        if self.color_transform is None:
            updates["color_transform"] = False

        resolved_color_transform = cast(
            "bool", updates.get("color_transform", self.color_transform)
        )
        # planar default is derived as (not color_transform):
        #   - non-color data, including 3-D z-stacks (layout 'zyx'): planar=True, so
        #     each component/slice is stored as its own plane — independent slices,
        #     NOT a color transform. This is the volumetric-stack path.
        #   - color data (3 components + MCT): planar=False, component-interleaved.
        # (This deliberately differs from the raw backend default of planar=True.)
        if self.planar is None:
            updates["planar"] = not resolved_color_transform

        if not updates:
            return self
        return replace(self, **updates)

    def validate(
        self,
        *,
        shape: tuple[int, ...],
        dtype: ZDType[TBaseDType, TBaseScalar],
        chunk_grid: ChunkGrid,
    ) -> None:
        del chunk_grid
        layout = self.layout or _default_layout(shape)
        native = dtype.to_native_dtype()

        if len(shape) not in {2, 3}:
            raise ValueError(
                f"OpenJPHCodec only supports 2-D or 3-D chunks, got shape {shape!r}"
            )
        if any(dim <= 0 for dim in shape):
            raise ValueError(
                f"OpenJPHCodec requires positive chunk dimensions, got {shape!r}"
            )
        if not _supported_native_dtype(native):
            raise ValueError(
                "OpenJPHCodec currently supports uint8, uint16, and int16 only; "
                f"got {native}"
            )

        if layout == "yx" and len(shape) != 2:
            raise ValueError(f"layout='yx' requires 2-D chunks, got shape {shape!r}")
        if layout != "yx" and len(shape) != 3:
            raise ValueError(
                f"layout={layout!r} requires 3-D chunks, got shape {shape!r}"
            )

        components = _component_count(shape, layout)
        if components < 1:
            raise ValueError(
                f"OpenJPHCodec requires at least one component, got shape {shape!r}"
            )

        if self.color_transform:
            if layout == "zyx":
                raise ValueError(
                    "color_transform=True is not valid for layout='zyx'; "
                    "that layout is intended for volumetric stacks, not color components"
                )
            if components != 3:
                raise ValueError(
                    "color_transform=True requires exactly 3 components, "
                    f"got {components}"
                )

        if self.qstep is not None and self.irreversible is False:
            raise ValueError("qstep is only valid for irreversible encoding")

    async def _encode_single(
        self,
        chunk_array: NDBuffer,
        chunk_spec: ArraySpec,
    ) -> Buffer | None:
        effective = self.evolve_from_array_spec(chunk_spec)
        layout = cast("Layout", effective.layout)
        backend = _get_backend()

        data = chunk_array.as_numpy_array()
        native_dtype = chunk_spec.dtype.to_native_dtype()
        if data.dtype != native_dtype:
            data = data.astype(native_dtype, copy=False)
        normalized = _normalize_for_backend(data, layout)

        encoded = await asyncio.to_thread(
            backend.encode,
            normalized,
            irreversible=cast("bool", effective.irreversible),
            qstep=effective.qstep,
            num_decompositions=cast("int", effective.num_decompositions),
            block_size=effective.block_size,
            progression_order=effective.progression_order,
            color_transform=cast("bool", effective.color_transform),
            planar=cast("bool", effective.planar),
        )
        return chunk_spec.prototype.buffer.from_bytes(encoded)

    async def _decode_single(
        self,
        chunk_bytes: Buffer,
        chunk_spec: ArraySpec,
    ) -> NDBuffer:
        effective = self.evolve_from_array_spec(chunk_spec)
        layout = cast("Layout", effective.layout)
        backend = _get_backend()

        native_dtype = chunk_spec.dtype.to_native_dtype()
        decoded = await asyncio.to_thread(
            backend.decode,
            chunk_bytes.to_bytes(),
        )
        arr = np.asarray(decoded)
        # A single-component codestream is ambiguous: the SIZ marker cannot
        # distinguish (h, w) from (1, h, w), so the backend returns 2-D and a
        # singleton component axis requested by the chunk spec must be restored
        # here. Only singleton axes are reconciled; any other mismatch still
        # fails the check below.
        expected_backend = _backend_shape(chunk_spec.shape, layout)
        if arr.shape != expected_backend and tuple(
            d for d in arr.shape if d != 1
        ) == tuple(d for d in expected_backend if d != 1):
            arr = arr.reshape(expected_backend)
        arr = _denormalize_from_backend(arr, layout)

        if arr.shape != chunk_spec.shape:
            raise ValueError(
                "OpenJPH backend returned an unexpected shape: "
                f"expected {chunk_spec.shape}, got {arr.shape}"
            )
        # The backend infers dtype from the codestream's SIZ marker, so for a
        # validated array this astype is a no-op; it only guards against a
        # backend/metadata disagreement (and never silently widens, since the
        # codestream stores the exact bit-depth/signedness it was written with).
        if arr.dtype != native_dtype:
            arr = arr.astype(native_dtype, copy=False)

        return chunk_spec.prototype.nd_buffer.from_ndarray_like(arr)

    def compute_encoded_size(
        self,
        _input_byte_length: int,
        _chunk_spec: ArraySpec,
    ) -> int:
        # HTJ2K output is variable-length, so the encoded size is not known ahead
        # of time. Raising NotImplementedError is the documented contract for a
        # variable-length codec (cf. zarr's built-in vlen codecs) and is never
        # called on the normal read/write path. The codec works fine as a chunk
        # data codec, including inside a shard; it simply cannot serve as a shard
        # *index* codec (which must be fixed-size).
        raise NotImplementedError("OpenJPH produces variable-length codestreams")
