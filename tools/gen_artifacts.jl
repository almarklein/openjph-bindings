# Generate julia/OpenJPH.jl/Artifacts.toml from a published GitHub release.
#
# Run this AFTER `release.yml` has published the per-platform tarballs for a tag,
# to bind libopenjph_c to those tarballs (lazy, platform-dispatched). Requires
# ArtifactUtils in the active environment:
#
#   julia -e 'import Pkg; Pkg.activate(temp=true); Pkg.add("ArtifactUtils")' \
#         tools/gen_artifacts.jl v0.1.0
#
# This is part of the D6 consumer switch and is NOT wired up until a release with
# the native binaries exists.

using ArtifactUtils
using Base.BinaryPlatforms

const REPO = "https://github.com/Clepio-Biotech/openjph-bindings"
const ARTIFACTS_TOML = normpath(joinpath(@__DIR__, "..", "julia", "OpenJPH.jl", "Artifacts.toml"))

# (release-asset stem, Julia platform) for each tarball release.yml publishes.
const TARGETS = [
    ("linux-x86_64",   Platform("x86_64",  "linux")),
    ("linux-aarch64",  Platform("aarch64", "linux")),
    ("macos-x86_64",   Platform("x86_64",  "macos")),
    ("macos-arm64",    Platform("aarch64", "macos")),
    ("windows-x86_64", Platform("x86_64",  "windows")),
]

# `asset_suffix` is "" for a tagged release (libopenjph_c-<stem>.tar.gz) and
# "-dev" for a PR dev pre-release (libopenjph_c-<stem>-dev.tar.gz).
function main(tag::AbstractString, asset_suffix::AbstractString = "")
    isfile(ARTIFACTS_TOML) && rm(ARTIFACTS_TOML)
    for (stem, platform) in TARGETS
        url = "$(REPO)/releases/download/$(tag)/libopenjph_c-$(stem)$(asset_suffix).tar.gz"
        @info "Binding libopenjph_c" platform url
        # Downloads the tarball, computes its tree hash + sha256, and records a
        # lazy, platform-constrained binding in Artifacts.toml.
        add_artifact!(ARTIFACTS_TOML, "libopenjph_c", url;
                      platform = platform, lazy = true, force = true)
    end
    @info "Wrote $(ARTIFACTS_TOML)"
end

isempty(ARGS) && error("usage: gen_artifacts.jl <release-tag> [asset-suffix]  e.g. v0.1.0  |  dev-pr-3 -dev")
main(ARGS[1], length(ARGS) >= 2 ? ARGS[2] : "")
