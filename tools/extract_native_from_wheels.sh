#!/usr/bin/env bash
# Repackage the native library that cibuildwheel already built into the wheels
# as one libopenjph_c-<platform>[<suffix>].tar.gz per platform, for the Julia
# Artifacts. This recycles the wheel build instead of rebuilding the binary —
# valid because OpenJPH is statically embedded, so the in-wheel lib has no
# external deps for auditwheel/delocate to vendor or rewrite.
#
# Usage: extract_native_from_wheels.sh <wheels_dir> <out_dir> [suffix]
#   suffix: "" for a tagged release, "-dev" for a PR dev pre-release.
set -euo pipefail

wheels_dir="${1:?usage: extract_native_from_wheels.sh <wheels_dir> <out_dir> [suffix]}"
out_dir="${2:?missing out_dir}"
suffix="${3:-}"
mkdir -p "$out_dir"

# Map a wheel filename to "<platform> <in-wheel-libname> <ext>". Only glibc
# (manylinux) Linux wheels are used; musllinux wheels are intentionally skipped.
map_platform() {
  case "$1" in
    *manylinux*x86_64*)  echo "linux-x86_64 libopenjph_c.so so" ;;
    *manylinux*aarch64*) echo "linux-aarch64 libopenjph_c.so so" ;;
    *macosx*x86_64*)     echo "macos-x86_64 libopenjph_c.dylib dylib" ;;
    *macosx*arm64*)      echo "macos-arm64 libopenjph_c.dylib dylib" ;;
    *win_amd64*)         echo "windows-x86_64 openjph_c.dll dll" ;;
  esac
}

found=0
while IFS= read -r whl; do
  mapping="$(map_platform "$(basename "$whl")")"
  [ -n "$mapping" ] || continue
  # shellcheck disable=SC2086
  set -- $mapping
  platform="$1"; libname="$2"; ext="$3"

  tarball="$out_dir/libopenjph_c-${platform}${suffix}.tar.gz"
  [ -f "$tarball" ] && continue   # one per platform (skip cp313 / extra ABI dups)

  tmp="$(mktemp -d)"
  unzip -o -j "$whl" "openjph/$libname" -d "$tmp" >/dev/null
  # Canonical name inside the tarball is always libopenjph_c.<ext> (only Windows,
  # whose in-wheel lib is openjph_c.dll, actually needs renaming).
  if [ "$libname" != "libopenjph_c.$ext" ]; then
    mv "$tmp/$libname" "$tmp/libopenjph_c.$ext"
  fi
  tar -czf "$tarball" -C "$tmp" "libopenjph_c.$ext"
  rm -rf "$tmp"
  echo "packaged $tarball  (from $(basename "$whl"))"
  found=$((found + 1))
done < <(find "$wheels_dir" -name '*.whl' | sort)

[ "$found" -gt 0 ] || { echo "ERROR: no native libs extracted from wheels in $wheels_dir" >&2; exit 1; }
echo "extracted $found platform tarball(s) into $out_dir"
