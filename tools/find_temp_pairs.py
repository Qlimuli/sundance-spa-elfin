"""Find encrypted pairs that change set_temp by exactly +/-0.5 C."""
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
        "sessionId": "b00787", "runId": "temp-find", "hypothesisId": "H21",
        "location": "find_temp_pairs.py", "message": msg,
        "data": data, "timestamp": int(time.time() * 1000),
    }, ensure_ascii=False)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    print(msg, data)


stubs()
from sundance_spa import CC_REQ, SpaClient  # noqa: E402


async def try_pair(c: SpaClient, btn: int, b6: int) -> float:
    before = c.status
    if not before:
        return 0.0
    await c.send_button(btn, CC_REQ, b6)
    await c.wait_status(6, 4.0)
    after = c.status
    if not after:
        return 0.0
    return round(after["set_temp"] - before["set_temp"], 1)


async def main() -> None:
    c = SpaClient("192.168.178.54", 8899)
    await c.connect()
    await c.wait_ready(12)
    log("start", {"set_temp": c.status["set_temp"] if c.status else None})

    up = down = None
    for logical in (225, 226):
        for btn in range(256):
            b6 = logical ^ btn ^ 1
            delta = await try_pair(c, btn, b6)
            if delta == 0.5 and up is None:
                up = (btn, b6, logical)
                log("found_up", {"btn": btn, "b6": b6, "logical": logical})
            elif delta == -0.5 and down is None:
                down = (btn, b6, logical)
                log("found_down", {"btn": btn, "b6": b6, "logical": logical})
            if up and down:
                break
        if up and down:
            break
            await asyncio.sleep(0.05)

    log("result", {"up": up, "down": down})
    await c.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
