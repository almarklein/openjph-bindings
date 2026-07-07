using OpenJPH
using Test

@testset "OpenJPH" begin

    @testset "Round-trip: $T" for T in (UInt8, Int8, UInt16, Int16, UInt32, Int32)
        data = T == UInt8  ? rand(T, 64, 128) :
               T == Int8   ? rand(T, 64, 128) :
               T == UInt16 ? rand(T, 64, 128) :
               T == Int16  ? rand(T, 64, 128) :
               T == UInt32 ? rand(UInt32(0):UInt32(10000), 64, 128) :
                             rand(Int32(-5000):Int32(5000), 64, 128)
        enc = openjph_encode(data)
        @test enc isa Vector{UInt8}
        @test length(enc) > 0
        dec = openjph_decode(enc)
        @test dec isa Array{T}
        @test dec == data
    end

    @testset "Round-trip: 3D UInt16" begin
        data = rand(UInt16, 3, 32, 64)
        enc  = openjph_encode(data)
        dec  = openjph_decode(enc)
        @test dec isa Array{UInt16}
        @test dec == data
    end

    @testset "3D non-color stack: independent slices round-trip" begin
        # Leading dim is a stack of independent slices (volumetric), not a color
        # transform; each slice must round-trip exactly in one codestream.
        Z, Y, X = 5, 16, 20
        data = zeros(UInt16, Z, Y, X)
        for s in 1:Z
            data[s, :, :] .= UInt16(s * 1000) .+ rand(UInt16(0):UInt16(400), Y, X)
        end
        dec = openjph_decode(openjph_encode(data))   # planar=true default
        @test size(dec) == size(data)
        @test all(data[s, :, :] == dec[s, :, :] for s in 1:Z)
    end

    @testset "Round-trip: 3D UInt16 color_transform" begin
        data = rand(UInt16, 3, 32, 64)
        enc  = openjph_encode(data; color_transform=true)
        dec  = openjph_decode(enc; color_transform=true)
        @test dec isa Array{UInt16}
        @test dec == data
    end

    @testset "Irreversible (lossy): UInt16" begin
        data = rand(UInt16, 64, 128)
        enc  = openjph_encode(data; irreversible=true)
        dec  = openjph_decode(enc)
        @test dec isa Array{UInt16}
        @test size(dec) == size(data)
        @test maximum(abs.(Int32.(dec) .- Int32.(data))) < div(typemax(UInt16), 4)
    end

    @testset "Output is independent of input buffer" begin
        data = rand(UInt16, 64, 64)
        enc  = openjph_encode(data)
        fill!(data, 0)
        dec = openjph_decode(enc)
        @test any(dec .!= 0)
    end

    # Non-contiguous inputs (views, transpose, strided views) must be encoded
    # correctly — the encoder materializes them to a contiguous buffer rather than
    # taking a pointer whose layout disagrees with size().
    @testset "Non-contiguous inputs encode correctly" begin
        A = rand(UInt16, 16, 24)
        inputs = Any[
            view(A, :, 2:10),   # contiguous view
            view(A, 2:10, :),   # strided view
            transpose(A),       # lazy transpose (not strided)
            reshape(A, 24, 16), # reshape
        ]
        for input in inputs
            reference = Array(input)
            @test openjph_decode(openjph_encode(input)) == reference
        end
    end

    # A corrupt/garbage/empty codestream must raise a clean error, not crash or
    # leak. Note: HTJ2K tolerates mid-stream truncation by design, so only
    # header-breaking corruption errors here.
    @testset "Corrupt codestream errors cleanly" begin
        enc = openjph_encode(rand(UInt16, 32, 48))
        @test_throws Exception openjph_decode(UInt8[])              # empty
        @test_throws Exception openjph_decode(rand(UInt8, 64))      # garbage
        @test_throws Exception openjph_decode(enc[1:10])            # truncated header
        bad = copy(enc); bad[end-5:end] .= 0x00
        @test_throws Exception openjph_decode(bad)                  # corrupt body
    end

    # OpenJPH-internal failures must surface the library's detailed diagnostic
    # (message text, source location) in the thrown error. OpenJPH's default
    # handler prints that detail to stderr and throws a generic "ojph error" —
    # the wrapper installs a capturing handler instead.
    @testset "Error messages carry OpenJPH detail" begin
        bad = vcat(UInt8[0xff, 0x4f], zeros(UInt8, 62))   # SOC, then garbage SIZ
        msg = try
            openjph_decode(bad)
            nothing
        catch e
            sprint(showerror, e)
        end
        @test msg !== nothing
        @test occursin("SIZ", msg)
        @test !occursin("ojph error", msg)
    end

    # An untrusted codestream claiming absurd dimensions must be rejected by the
    # native size guard rather than attempting a huge allocation. Patch the SIZ
    # Xsiz/Ysiz/XTsiz/YTsiz fields to a huge value.
    @testset "Oversized dimensions rejected" begin
        enc = copy(openjph_encode(rand(UInt16, 32, 48)))
        @test enc[1:4] == UInt8[0xff, 0x4f, 0xff, 0x51]   # SOC + SIZ, layout assumption
        be(v) = UInt8[(v >> 24) & 0xff, (v >> 16) & 0xff, (v >> 8) & 0xff, v & 0xff]
        for off in (8, 12, 24, 28)        # Xsiz, Ysiz, XTsiz, YTsiz (0-based byte offsets)
            enc[off+1:off+4] .= be(0x40000000)
        end
        @test_throws Exception openjph_decode(enc)
    end

    # FFI-boundary leak regression: C-allocated buffers are copied and freed on
    # every call, so a leak is invisible to Julia's GC and shows up only as
    # unbounded RSS growth. Statistical complement to the deterministic
    # LeakSanitizer driver in native/tests/leak_check.c. Linux-only (statm).
    @testset "Memory: encode/decode RSS stable" begin
        if !Sys.islinux()
            @info "RSS leak check skipped (/proc/self/statm is Linux-only)"
        else
            page  = Int(ccall(:getpagesize, Cint, ()))
            rss() = parse(Int, split(read("/proc/self/statm", String))[2]) * page
            data  = rand(UInt16, 256, 256)                       # 128 KiB raw
            bad   = vcat(UInt8[0xff, 0x4f], zeros(UInt8, 62))
            function cycle()
                dec = openjph_decode(openjph_encode(data))
                @assert size(dec) == size(data)
                try                                              # error path must
                    openjph_decode(bad)                          # not leak either
                catch
                end
            end
            for _ in 1:200; cycle(); end                         # warmup
            GC.gc(true); GC.gc(true)
            baseline = rss()
            for _ in 1:2000; cycle(); end
            GC.gc(true); GC.gc(true)
            grown = rss() - baseline
            # A leaked 128 KiB buffer per cycle would grow RSS by >= 250 MiB;
            # 64 MiB tolerates GC-pool/allocator noise with ~4x margin.
            @test grown < 64 * 1024 * 1024
        end
    end

end
