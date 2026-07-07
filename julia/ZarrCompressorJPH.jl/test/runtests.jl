using ZarrCompressorJPH
using Zarr
import Zarr.Codecs.V3Codecs as V3Codecs
using Test
import JSON

@testset "ZarrCompressorJPH" begin

    @testset "HTJ2KCodec defaults" begin
        c = HTJ2KCodec()
        @test c.irreversible == false
        @test c.qstep === 0f0
        @test c.num_decompositions == 5
        @test c.block_width == 64
        @test c.block_height == 64
        @test c.progression_order == "LRCP"
        @test c.color_transform == false
        @test c.planar == true
    end

    @testset "JSON serialization round-trip" begin
        c = HTJ2KCodec()
        d = JSON.lower(c)
        @test d["name"] == "openjph_htj2k"
        cfg = d["configuration"]
        @test cfg["irreversible"] == false
        @test !haskey(cfg, "qstep")   # omitted when 0f0
        @test cfg["num_decompositions"] == 5
        @test cfg["block_size"] == [64, 64]   # [width, height], matches Python
        @test cfg["progression_order"] == "LRCP"

        # round-trip via the registered parser
        c2 = V3Codecs.codec_parsers["openjph_htj2k"].parser(cfg, nothing)
        @test c2.irreversible       == c.irreversible
        @test c2.qstep              == c.qstep
        @test c2.num_decompositions == c.num_decompositions
        @test c2.block_width        == c.block_width
        @test c2.block_height       == c.block_height
        @test c2.progression_order  == c.progression_order
        @test c2.color_transform    == c.color_transform
        @test c2.planar             == c.planar
    end

    @testset "Non-square block_size is not transposed" begin
        c   = HTJ2KCodec(block_width = 32, block_height = 64)
        cfg = JSON.lower(c)["configuration"]
        @test cfg["block_size"] == [32, 64]   # [width, height]
        c2  = V3Codecs.codec_parsers["openjph_htj2k"].parser(cfg, nothing)
        @test c2.block_width  == 32
        @test c2.block_height == 64
    end

    @testset "qstep serialized when non-zero" begin
        c = HTJ2KCodec(irreversible=true, qstep=0.01f0)
        d = JSON.lower(c)
        @test haskey(d["configuration"], "qstep")
        @test d["configuration"]["qstep"] ≈ 0.01f0
    end

    @testset "codec registered with Zarr" begin
        @test haskey(V3Codecs.codec_parsers, "openjph_htj2k")
    end

    @testset "codec_encode / codec_decode — 2D UInt16 lossless" begin
        c    = HTJ2KCodec()
        data = rand(UInt16, 64, 128)
        enc  = V3Codecs.codec_encode(c, data)
        @test enc isa Vector{UInt8}
        @test length(enc) > 0
        dec  = V3Codecs.codec_decode(c, enc, UInt16, (64, 128))
        @test dec == data
    end

    @testset "codec_decode rejects shape/eltype mismatch" begin
        c    = HTJ2KCodec()
        data = rand(UInt16, 32, 64)
        enc  = V3Codecs.codec_encode(c, data)
        @test V3Codecs.codec_decode(c, enc, UInt16, (32, 64)) == data
        @test_throws Exception V3Codecs.codec_decode(c, enc, UInt16, (64, 32))  # wrong shape
        @test_throws Exception V3Codecs.codec_decode(c, enc, Int16, (32, 64))   # wrong eltype
    end

    @testset "codec_encode / codec_decode — 3D UInt16 lossless" begin
        c    = HTJ2KCodec()
        data = rand(UInt16, 3, 32, 64)
        enc  = V3Codecs.codec_encode(c, data)
        dec  = V3Codecs.codec_decode(c, enc, UInt16, (3, 32, 64))
        @test dec == data
    end

    @testset "codec_decode — trailing-singleton component chunk" begin
        # (w, h, 1) encodes to a 1-component codestream whose SIZ marker is
        # indistinguishable from (w, h); decode must restore the singleton
        # axis Zarr asked for instead of erroring at read time.
        c    = HTJ2KCodec()
        data = rand(UInt16, 32, 64, 1)
        enc  = V3Codecs.codec_encode(c, data)
        dec  = V3Codecs.codec_decode(c, enc, UInt16, (32, 64, 1))
        @test size(dec) == (32, 64, 1)
        @test dec == data
        # non-singleton mismatches must still error
        @test_throws Exception V3Codecs.codec_decode(c, enc, UInt16, (64, 32, 1))
    end

    @testset "codec_encode / codec_decode — irreversible (lossy)" begin
        c    = HTJ2KCodec(irreversible=true)
        data = rand(UInt16, 32, 64)
        enc  = V3Codecs.codec_encode(c, data)
        dec  = V3Codecs.codec_decode(c, enc, UInt16, (32, 64))
        @test size(dec) == size(data)
        @test maximum(abs.(Int32.(dec) .- Int32.(data))) < div(typemax(UInt16), 4)
    end

    @testset "zcreate_htj2k — 2D UInt16 lossless" begin
        c        = HTJ2KCodec()
        z        = zcreate_htj2k(UInt16, 256, 128; codec=c, chunks=(64, 64))
        original = rand(UInt16, 256, 128)
        z[:, :]  = original
        @test z[:, :] == original
    end

    @testset "zcreate_htj2k — 3D UInt16 lossless" begin
        c        = HTJ2KCodec()
        z        = zcreate_htj2k(UInt16, 3, 64, 128; codec=c, chunks=(3, 64, 64))
        original = rand(UInt16, 3, 64, 128)
        z[:, :, :] = original
        @test z[:, :, :] == original
    end

    @testset "zcreate_htj2k — singleton-component chunks" begin
        # The PR #3 bug shape: a non-singleton array stored with chunks whose
        # component axis is 1, so every chunk is a 1-component codestream.
        c        = HTJ2KCodec()
        z        = zcreate_htj2k(UInt16, 64, 128, 4; codec=c, chunks=(64, 128, 1))
        original = rand(UInt16, 64, 128, 4)
        z[:, :, :] = original
        @test z[:, :, :] == original
    end

    @testset "zcreate_htj2k — persistent store round-trip" begin
        c    = HTJ2KCodec()
        path = tempname()
        z    = zcreate_htj2k(UInt16, 64, 64; codec=c, path=path)
        original = rand(UInt16, 64, 64)
        z[:, :] = original
        z2 = zopen(path, "r"; zarr_format=3)
        @test z2[:, :] == original
    end

end
