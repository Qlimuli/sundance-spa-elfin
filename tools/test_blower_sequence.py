"""Controlled blower test: all off -> blower on -> blower off."""
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


def _install_ha_stubs() -> None:
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


def log(msg: str, data: dict, hid: str) -> None:
    line = json.dumps({
        "sessionId": "b00787", "runId": "blower-seq", "hypothesisId": hid,
        "location": "test_blower_sequence.py", "message": msg,
        "data": data, "timestamp": int(time.time() * 1000),
    }, ensure_ascii=False)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    print(f"{msg}: {data}")


def snap(st: dict | None) -> dict:
    if not st:
        return {}
    raw = st.get("raw", [])
    return {
        "p1": st["pump1"], "p2": st["pump2"],
        "circ_m": st["circ_manual"], "circ_r": st["circ_running"],
        "d1": raw[1] if len(raw) > 1 else None,
        "d2": raw[2] if len(raw) > 2 else None,
        "d4": raw[4] if len(raw) > 4 else None,
        "d13": raw[13] if len(raw) > 13 else None,
        "d14": raw[14] if len(raw) > 14 else None,
    }


_install_ha_stubs()

from sundance_spa import (  # noqa: E402
    BTN_BLOWER,
    BTN_CLEARRAY,
    BTN_PUMP1,
    BTN_PUMP2,
    CC_REQ,
    CC_REQ_ALT,
    SpaClient,
)


async def press(client: SpaClient, btn: int, mtype: int = CC_REQ) -> dict:
    before = snap(client.status)
    await client.send_button(btn, mtype)
    await client.wait_status(4, 5.0)
    after = snap(client.status)
    log("press", {"btn": btn, "mtype": mtype, "before": before, "after": after}, "H11")
    return after


async def ensure_off(client: SpaClient) -> None:
    log("phase", {"step": "ensure_off_start", "status": snap(client.status)}, "H11")
    for _ in range(3):
        st = client.status
        if not st:
            await asyncio.sleep(1)
            continue
        if st["pump1"]:
            await press(client, BTN_PUMP1)
        if st["pump2"]:
            await press(client, BTN_PUMP2)
        if st["circ_manual"]:
            await press(client, BTN_CLEARRAY)
        await asyncio.sleep(1.5)
    log("phase", {"step": "ensure_off_done", "status": snap(client.status)}, "H11")


async def main() -> None:
    client = SpaClient("192.168.178.54", 8899)
    await client.connect()
    await client.wait_ready(12)
    log("connected", {"channel": client._assigned_channel}, "H11")

    await ensure_off(client)
    await asyncio.sleep(2)

    # User-Hinweis: Pumpe1 (228) schaltete Blubber ein
    log("phase", {"step": "blower_on"}, "H12")
    await press(client, BTN_PUMP1)
    await asyncio.sleep(6)

    log("phase", {"step": "blower_off"}, "H12")
    await press(client, BTN_PUMP1)
    await asyncio.sleep(3)

    # Vergleich: 243 CC und 243 0x17 (sollten laut früheren Tests nichts tun)
    await ensure_off(client)
    await asyncio.sleep(2)
    log("phase", {"step": "try_243_cc"}, "H13")
    await press(client, BTN_BLOWER, CC_REQ)
    await asyncio.sleep(4)
    await press(client, BTN_BLOWER, CC_REQ)

    await client.disconnect()
    print("Sequenz fertig.")


if __name__ == "__main__":
    asyncio.run(main())
