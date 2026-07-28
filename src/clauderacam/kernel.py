"""Measurement-kernel dispatcher.

The kernel is pure measurement: it carves the stock and reports what
physically happened, per move. ALL check logic lives in verify.py and
physics.py where it can be read against the constitution.

Two implementations exist and MUST agree (tests/kernel_parity.py):
  - clauderacam_kernel: the Rust extension (kernel/ crate), ~50x faster
  - kernel_py:          the pure-Python reference

The Python reference is the semantic authority; the Rust kernel is an
optimization of it. A change to one without the other fails parity.
"""
from __future__ import annotations

try:
    import clauderacam_kernel as _rust
except ImportError:  # pure-Python fallback (slow, always available)
    _rust = None

from . import kernel_py

BACKEND = "rust" if _rust is not None else "python"


def measure(**kw):
    if _rust is not None:
        return _rust.measure(**kw)
    return kernel_py.measure(**kw)


def measure_py(**kw):
    """Always the Python reference — used by the parity test."""
    return kernel_py.measure(**kw)


def measure_rust(**kw):
    """Always the Rust kernel; raises if the extension is not built."""
    if _rust is None:
        raise RuntimeError("clauderacam_kernel extension not built")
    return _rust.measure(**kw)
