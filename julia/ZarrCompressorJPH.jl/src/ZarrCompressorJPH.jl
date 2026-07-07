module ZarrCompressorJPH

using OpenJPH
using Zarr
import Zarr.Codecs.V3Codecs as V3Codecs
import JSON

"""
    HTJ2KCodec(; kwargs...)

HTJ2K (High Throughput JPEG 2000) array-to-bytes codec for Zarr v3.
Wraps OpenJPH for lossless (default) or lossy chunk compression.

# Keyword arguments
- `irreversible::Bool = false` — lossless (false) or lossy 9/7 wavelet (true)
- `qstep::Float32 = 0f0` — quantization step used when `irreversible=true` and `> 0`
- `num_decompositions::Int = 5`
- `block_width::Int = 64`, `block_height::Int = 64`
- `progression_order::String = "LRCP"`
- `color_transform::Bool = false` — MCT for 3-component images
- `planar::Bool = true`
"""
struct HTJ2KCodec <: V3Codecs.V3Codec{:array, :bytes}
    irreversible        :: Bool
    qstep               :: Float32
    num_decompositions  :: Int
    block_width         :: Int
    block_height        :: Int
    progression_order   :: String
    color_transform     :: Bool
    planar              :: Bool
end

function HTJ2KCodec(;
        irreversible::Bool = false,
        qstep::Float32 = 0f0,
        num_decompositions::Int = 5,
        block_width::Int = 64,
        block_height::Int = 64,
        progression_order::String = "LRCP",
        color_transform::Bool = false,
        planar::Bool = true)
    HTJ2KCodec(irreversible, qstep, num_decompositions,
               block_width, block_height, progression_order,
               color_transform, planar)
end

V3Codecs.name(::HTJ2KCodec) = "openjph_htj2k"

function V3Codecs.codec_encode(c::HTJ2KCodec, data::AbstractArray)
    openjph_encode(data;
        irreversible       = c.irreversible,
        qstep              = c.qstep == 0f0 ? nothing : c.qstep,
        num_decompositions = c.num_decompositions,
        block_width        = c.block_width,
        block_height       = c.block_height,
        progression_order  = c.progression_order,
        color_transform    = c.color_transform,
        planar             = c.planar)
end

_nonsingleton(t) = Tuple(d for d in t if d != 1)

function V3Codecs.codec_decode(c::HTJ2KCodec, encoded::Vector{UInt8},
        ::Type{T}, dims::NTuple{N, Int64};
        fill_value = nothing) where {T, N}
    _ = fill_value
    raw = openjph_decode(encoded; color_transform = c.color_transform)
    # The element type and shape come from the codestream's SIZ marker. Validate
    # them against what Zarr expects: a mismatch (corruption / writer bug) would
    # otherwise be silently linear-copied or converted by Zarr's copyto!.
    eltype(raw) === T || error(
        "HTJ2KCodec decode: element type mismatch — codestream has $(eltype(raw)), expected $T")
    size(raw) == dims && return raw
    # A single-component codestream is ambiguous: the SIZ marker cannot
    # distinguish (w, h) from (w, h, 1) (in Julia's column-major convention the
    # singleton component axis is trailing), so openjph_decode returns 2-D and
    # the singleton axis requested by Zarr's chunk dims must be restored here.
    # Only singleton axes are reconciled; any other mismatch is still an error.
    _nonsingleton(size(raw)) == _nonsingleton(dims) && return reshape(raw, dims)
    error("HTJ2KCodec decode: shape mismatch — codestream has $(size(raw)), expected $dims")
end

function JSON.lower(c::HTJ2KCodec)
    cfg = Dict{String, Any}(
        "irreversible"       => c.irreversible,
        "num_decompositions" => c.num_decompositions,
        # Canonical on-disk form is block_size = [width, height], matching the
        # Python codec and OpenJPH's set_block_dims(width, height).
        "block_size"         => [c.block_width, c.block_height],
        "progression_order"  => c.progression_order,
        "color_transform"    => c.color_transform,
        "planar"             => c.planar,
    )
    c.qstep != 0f0 && (cfg["qstep"] = c.qstep)
    Dict("name" => "openjph_htj2k", "configuration" => cfg)
end

"""
    zcreate_htj2k(T, dims...; codec=HTJ2KCodec(), chunks=dims, path=nothing)

Create a Zarr v3 array with element type `T` and dimensions `dims`, using
`HTJ2KCodec` as the array-to-bytes codec.

`path` is the directory path for a persistent store; `nothing` (default) creates
an in-memory store. `chunks` defaults to `dims` (single chunk).
"""
function zcreate_htj2k(::Type{T}, dims::Integer...;
        codec::HTJ2KCodec = HTJ2KCodec(),
        chunks = dims,
        path::Union{String, Nothing} = nothing,
        fill_value = zero(T)) where T
    N = length(dims)
    pipeline = Zarr.V3Pipeline((), codec, ())
    cke = Zarr.ChunkKeyEncoding('/', true)
    md = Zarr.MetadataV3{T, N, typeof(pipeline), typeof(cke)}(
        3, "array",
        NTuple{N, Int}(dims),
        NTuple{N, Int}(chunks),
        Zarr.typestr3(T),
        pipeline,
        fill_value,
        cke
    )
    store = path === nothing ? Zarr.DictStore() : Zarr.DirectoryStore(path)
    v = Zarr.ZarrFormat(Val(3))
    Zarr.writemetadata(v, store, "", md)
    Zarr.writeattrs(v, store, "", Dict())
    Zarr.ZArray(md, store, "", Dict(), true)
end

function _parse_htj2k_config(config)
    # Canonical block_size = [width, height]. Fall back to the legacy
    # block_width/block_height keys for back-compat with older Julia-written configs.
    block_size = get(config, "block_size", nothing)
    block_width  = block_size !== nothing ? Int(block_size[1]) : Int(get(config, "block_width",  64))
    block_height = block_size !== nothing ? Int(block_size[2]) : Int(get(config, "block_height", 64))
    HTJ2KCodec(;
        irreversible       = Bool(get(config, "irreversible", false)),
        qstep              = Float32(get(config, "qstep", 0f0)),
        num_decompositions = Int(get(config, "num_decompositions", 5)),
        block_width,
        block_height,
        progression_order  = String(get(config, "progression_order", "LRCP")),
        color_transform    = Bool(get(config, "color_transform", false)),
        planar             = Bool(get(config, "planar", true)),
    )
end

function __init__()
    V3Codecs.register_codec("openjph_htj2k", HTJ2KCodec) do config, _
        _parse_htj2k_config(config)
    end
end

export HTJ2KCodec, zcreate_htj2k

end # module
