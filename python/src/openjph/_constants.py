from __future__ import annotations

# Canonical HTJ2K progression orders. Shared by the low-level backend (_backend)
# and the Zarr codec (zarr), so the single source of truth lives here — the Zarr
# codec must validate these without importing the native backend.
PROGRESSION_ORDERS: tuple[str, ...] = ("LRCP", "RLCP", "RPCL", "PCRL", "CPRL")
