"""Blubber nur mit verschlüsseltem Panel-CC (139/102) – keine Pumpen."""
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
    co.Platform = type("Platform", (), {
        "CLIMATE": "c", "SWITCH": "s", "LIGHT": "l", "SENSOR": "s",
    })
    cr = types.ModuleType("homeassistant.core")
    cr.HomeAssistant = type("HomeAssistant", (), {})
    um = types.ModuleType("homeassistant.helpers.update_coordinator")
    um.UpdateFailed = type("UpdateFailed", (Exception,), {})
    um.DataUpdateCoordinator = type("DataUpdateCoordinator", (), {
        "__init__": lambda self, *a, **k: None,
    })
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
        "sessionId": "b00787", "runId": "blower-enc", "hypothesisId": "H19",
        "message": msg, "data": data, "timestamp": int(time.time() * 1000),
    }, ensure_ascii=False)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    print(msg, data)


def snap(st: dict | None) -> dict:
    if not st:
        return {}
    r = st.get("raw", [])
    return {"p1": st["pump1"], "p2": st["pump2"], "d4": r[4] if len(r) > 4 else None}


stubs()

from sundance_spa import SpaClient  # noqa: E402


async def main() -> None:
    client = SpaClient("192.168.178.54", 8899)
    await client.connect()
    await client.wait_ready(12)
    log("start", snap(client.status))

    print("--- BLUBBER EIN (Panel 139/102) 15s ---")
    await client.send_blower_toggle()
    await client.wait_status(4, 5)
    log("after_on", snap(client.status))
    await asyncio.sleep(15)

    print("--- BLUBBER AUS (Panel 139/102) ---")
    await client.send_blower_toggle()
    await client.wait_status(4, 5)
    log("after_off", snap(client.status))

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
