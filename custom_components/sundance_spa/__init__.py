"""
Sundance / Balboa Spa – Home Assistant Integration
Protokoll-Engine + DataUpdateCoordinator (v1.4.4 native Cameo temp control).

The full engine is stored gzip+base64 in spa_engine_data.py and expanded on first import.
"""
from __future__ import annotations

import gzip
import base64
import sys
import types
from pathlib import Path

def _load_engine() -> types.ModuleType:
    from . import spa_engine_data
    raw = gzip.decompress(base64.b64decode(spa_engine_data.ENGINE_B64))
    # Write expanded module next to this file for debuggability
    target = Path(__file__).with_name("spa_engine.py")
    if not target.exists() or target.stat().st_size < 1000:
        target.write_bytes(raw)
    # Load as real module
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        f"{__name__}.spa_engine", target
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod

_engine = _load_engine()

# Re-export public API expected by climate/switch/light/sensor platforms
DOMAIN = _engine.DOMAIN
PLATFORMS = _engine.PLATFORMS
SpaClient = _engine.SpaClient
SpaCoordinator = _engine.SpaCoordinator
async_setup_entry = _engine.async_setup_entry
async_unload_entry = _engine.async_unload_entry

__all__ = [
    "DOMAIN",
    "PLATFORMS",
    "SpaClient",
    "SpaCoordinator",
    "async_setup_entry",
    "async_unload_entry",
]
