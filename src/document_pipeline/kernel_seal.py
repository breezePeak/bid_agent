"""Capability seal for trusted-kernel-only ControlStore mutations.

ValidationReport and GateReceipt rows may only be written by services that hold
this seal. Promotion still re-validates Store content independently so a stolen
seal alone cannot launder invalid payloads.
"""

from __future__ import annotations

# Opaque singleton. Only document_pipeline trusted services import and pass it.
KERNEL_SEAL = object()
