"""FFI-boundary memory-leak regression test.

The C API transfers buffer ownership across the FFI (openjph_free), so a leak
would be invisible to Python-level tooling — it shows up only as unbounded RSS
growth. This test is statistical (RSS slope over many cycles) and complements
the deterministic C-level LeakSanitizer driver in native/tests/leak_check.c.

Linux-only: RSS is read from /proc/self/statm. All CI test jobs and manylinux
wheel tests run on Linux; macOS/Windows wheel tests skip cleanly.
"""

from __future__ import annotations

import gc
import os
import sys

import numpy as np
import pytest

openjph_backend = pytest.importorskip("openjph._backend")

RNG = np.random.default_rng(42)

WARMUP_CYCLES = 200
MEASURED_CYCLES = 2000
# One leaked 128 KiB buffer per cycle would grow RSS by >= 250 MiB over the
# measured cycles; 32 MiB tolerates allocator arena noise with ~8x margin.
MAX_RSS_GROWTH = 32 * 1024 * 1024


def _rss() -> int:
    page = os.sysconf("SC_PAGE_SIZE")
    with open("/proc/self/statm") as f:
        return int(f.read().split()[1]) * page


@pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="/proc/self/statm is Linux-only"
)
def test_encode_decode_rss_stable() -> None:
    data = RNG.integers(0, 60_000, size=(256, 256), dtype=np.uint16)  # 128 KiB raw
    garbage = b"\xff\x4f" + b"\x00" * 62

    def one_cycle() -> None:
        decoded = openjph_backend.decode(openjph_backend.encode(data))
        assert decoded.shape == data.shape
        # The error path must not leak either.
        with pytest.raises(RuntimeError):
            openjph_backend.decode(garbage)

    for _ in range(WARMUP_CYCLES):
        one_cycle()
    gc.collect()
    baseline = _rss()

    for _ in range(MEASURED_CYCLES):
        one_cycle()
    gc.collect()
    grown = _rss() - baseline

    assert grown < MAX_RSS_GROWTH, (
        f"RSS grew {grown / 2**20:.1f} MiB over {MEASURED_CYCLES} encode/decode "
        f"cycles (limit {MAX_RSS_GROWTH / 2**20:.0f} MiB) — likely an FFI leak"
    )
