"""Find encrypted CC pairs for temp up (225) / down (226)."""
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


def log(msg: str, data: dict) -> None:
    line = json.dumps({
        "sessionId": "b00787", "runId": "temp-hunt", "hypothesisId": "H21",
        "location": "hunt_temp_buttons.py", "message": msg,
        "data": data, "timestamp": int(time.time() * 1000),
    }, ensure_ascii=False)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    print(msg, data)


stubs()
from sundance_spa import CC_REQ, SpaClient  # noqa: E402


async def try_logical(c: SpaClient, logical: int, btn: int) -> bool:
    b6 = logical ^ btn ^ 1
    before = c.status
    if not before:
        return False
    b_set = before["set_temp"]
    b_raw = before["raw_d8"]
    await c.send_button(btn, CC_REQ, b6)
    await c.wait_status(8, 5.0)
    after = c.status
    if not after:
        return False
    changed = after["set_temp"] != b_set or after["raw_d8"] != b_raw
    if changed:
        log("hit", {
            "logical": logical, "btn": btn, "b6": b6,
            "before": b_set, "after": after["set_temp"],
            "raw_before": b_raw, "raw_after": after["raw_d8"],
        })
    return changed


async def main() -> None:
    c = SpaClient("192.168.178.54", 8899)
    await c.connect()
    await c.wait_ready(12)
    log("start", {"set_temp": c.status["set_temp"] if c.status else None})

    hits: dict[int, tuple[int, int]] = {}
    for logical in (225, 226):
        for btn in range(256):
            if await try_logical(c, logical, btn):
                b6 = logical ^ btn ^ 1
                hits[logical] = (btn, b6)
                break
            await asyncio.sleep(0.15)
        log("logical_done", {"logical": logical, "hit": hits.get(logical)})

    log("summary", {"hits": {str(k): list(v) for k, v in hits.items()}})
    await c.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
