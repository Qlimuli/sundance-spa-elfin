"""Quick scan: find +/-0.5C temp pairs (focused btn range)."""
from __future__ import annotations

import asyncio
import json
import sys
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "custom_components"))
LOG = ROOT.parent / "debug-b00787.log"


def stubs() -> None:
    ha = types.ModuleType("homeassistant")
    ce = types.ModuleType("homeassistant.config_entries")
    ce.ConfigEntry = type("ConfigEntry", (), {})
    co = types.ModuleType("homeassistant.const")
    co.CONF_HOST, co.CONF_PORT = "host", "port"
    co.Platform = type("Platform", (), {"CLIMATE": "c", "SWITCH": "s", "LIGHT": "l", "SENSOR": "s"})
    cr = types.ModuleType("homeassistant.core")
    cr.HomeAssistant = type("HomeAssistant", (), {})
    um = types.ModuleType("homeassistant.helpers.update_coordinator")
    um.UpdateFailed = type("UpdateFailed", (Exception,), {})
    um.DataUpdateCoordinator = type("DataUpdateCoordinator", (), {"__init__": lambda s, *a, **k: None})
    sys.modules.update({
        "homeassistant": ha,
        "homeassistant.config_entries": ce,
        "homeassistant.const": co,
        "homeassistant.core": cr,
        "homeassistant.helpers": types.ModuleType("homeassistant.helpers"),
        "homeassistant.helpers.update_coordinator": um,
    })


stubs()
from sundance_spa import CC_REQ, SpaClient  # noqa: E402


async def main() -> None:
    c = SpaClient("192.168.178.54", 8899)
    await c.connect()
    await c.wait_ready(12)
    hits: list[dict] = []
    start = c.status["set_temp"] if c.status else None
    print("start", start, flush=True)
    for logical in (225, 226):
        for btn in range(256):
            b6 = logical ^ btn ^ 1
            before = c.status
            if not before:
                continue
            await c.send_button(btn, CC_REQ, b6)
            await c.wait_status(5, 3.0)
            after = c.status
            if not after:
                continue
            delta = round(after["set_temp"] - before["set_temp"], 1)
            if delta != 0:
                hit = {"logical": logical, "btn": btn, "b6": b6, "delta": delta}
                hits.append(hit)
                print(hit, flush=True)
            await asyncio.sleep(0.05)
    summary = {"start": start, "hits": hits}
    print("SUMMARY", summary, flush=True)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "sessionId": "b00787", "runId": "temp-map", "hypothesisId": "H21",
            "location": "map_temp_deltas.py", "message": "summary",
            "data": summary, "timestamp": int(time.time() * 1000),
        }, ensure_ascii=False) + "\n")
    await c.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
