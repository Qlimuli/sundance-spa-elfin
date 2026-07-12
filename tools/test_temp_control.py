"""Test temp control: plain 225/226 vs encrypted pairs."""
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


def log(msg: str, data: dict, hid: str) -> None:
    line = json.dumps({
        "sessionId": "b00787", "runId": "temp-test", "hypothesisId": hid,
        "location": "test_temp_control.py", "message": msg,
        "data": data, "timestamp": int(time.time() * 1000),
    }, ensure_ascii=False)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    print(msg, data)


stubs()
from sundance_spa import BTN_TEMP_UP, BTN_TEMP_DOWN, CC_REQ, SpaClient  # noqa: E402


async def snap(c: SpaClient) -> dict:
    s = c.status
    if not s:
        return {}
    return {"set_temp": s["set_temp"], "raw_d8": s["raw_d8"], "p1": s["pump1"], "p2": s["pump2"]}


async def press(c: SpaClient, btn: int, b6: int = 0, label: str = "") -> dict:
    before = await snap(c)
    await c.send_button(btn, CC_REQ, b6)
    await c.wait_status(6, 6.0)
    after = await snap(c)
    log("press", {"label": label, "btn": btn, "b6": b6, "decoded": btn ^ b6 ^ 1, "before": before, "after": after}, "H24")
    return after


async def main() -> None:
    c = SpaClient("192.168.178.54", 8899)
    await c.connect()
    await c.wait_ready(12)
    log("start", await snap(c), "H24")

    # Test 1: plain temp up (225, b6=0)
    await press(c, BTN_TEMP_UP, 0, "plain_225_up")

    # Test 2: plain temp down (226, b6=0)
    await press(c, BTN_TEMP_DOWN, 0, "plain_226_down")

    # Test 3: set_temperature feedback loop
    cur = c.status["set_temp"] if c.status else 34.0
    target = cur + 1.0
    log("set_temp_call", {"target": target, "before": await snap(c)}, "H23")
    try:
        await c.set_temperature(target)
        log("set_temp_ok", await snap(c), "H23")
    except Exception as exc:
        log("set_temp_fail", {"error": str(exc), "after": await snap(c)}, "H23")

    await c.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
