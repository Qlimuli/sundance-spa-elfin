"""Pumpe2 + Blubber aus, dann Blubber wieder ein."""
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
        "sessionId": "b00787", "runId": "user-seq", "hypothesisId": "H16",
        "message": msg, "data": data, "timestamp": int(time.time() * 1000),
    }, ensure_ascii=False)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    print(msg, data)


def snap(st: dict | None) -> dict:
    if not st:
        return {}
    r = st.get("raw", [])
    return {
        "p1": st["pump1"], "p2": st["pump2"],
        "circ_m": st["circ_manual"], "d2": r[2] if len(r) > 2 else None,
        "d4": r[4] if len(r) > 4 else None, "d14": r[14] if len(r) > 14 else None,
    }


stubs()

from sundance_spa import (  # noqa: E402
    BTN_BLOWER,
    BTN_CLEARRAY,
    BTN_PUMP2,
    CC_REQ,
    CC_REQ_ALT,
    SpaClient,
)


async def press(client: SpaClient, btn: int, mtype: int = CC_REQ, label: str = "") -> None:
    before = snap(client.status)
    await client.send_button(btn, mtype)
    await client.wait_status(4, 5.0)
    after = snap(client.status)
    log(label or f"press_{btn}", {"btn": btn, "mtype": mtype, "before": before, "after": after})


async def ensure_p2_off(client: SpaClient) -> None:
    for _ in range(3):
        st = client.status
        if not st or not st["pump2"]:
            return
        await press(client, BTN_PUMP2, CC_REQ, "pump2_off")
        await asyncio.sleep(2)


async def blower_toggle(client: SpaClient, mtype: int, label: str) -> None:
    await press(client, BTN_BLOWER, mtype, label)


async def main() -> None:
    client = SpaClient("192.168.178.54", 8899)
    await client.connect()
    await client.wait_ready(12)
    log("start", snap(client.status))

    # 1) Pumpe 2 aus
    await ensure_p2_off(client)
    await asyncio.sleep(1)

    # 2) Blubber aus (243 CC, dann 243 0x17 falls nötig)
    log("phase", {"step": "blower_off"})
    await blower_toggle(client, CC_REQ, "blower_off_cc")
    await asyncio.sleep(2)
    await blower_toggle(client, CC_REQ_ALT, "blower_off_17")
    await asyncio.sleep(2)

    # ClearRay/Wasserfall aus falls aktiv
    st = client.status
    if st and st["circ_manual"]:
        await press(client, BTN_CLEARRAY, CC_REQ, "clearray_off")

    log("mid", snap(client.status))
    await asyncio.sleep(2)

    # 3) Blubber wieder ein
    log("phase", {"step": "blower_on"})
    await blower_toggle(client, CC_REQ_ALT, "blower_on_17")
    await asyncio.sleep(3)
    st = client.status
    if st:  # falls 0x17 nicht reagiert, auch 0xCC probieren
        await blower_toggle(client, CC_REQ, "blower_on_cc")

    log("end", snap(client.status))
    await client.disconnect()
    print("Fertig.")


if __name__ == "__main__":
    asyncio.run(main())
