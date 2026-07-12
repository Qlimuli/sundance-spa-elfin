"""Alles aus: Wasserfall (239), Blubber-Kandidaten, Pumpen."""
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
        "sessionId": "b00787", "runId": "all-off", "hypothesisId": "H17",
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
        "circ_m": st["circ_manual"], "circ_r": st["circ_running"],
        "d2": r[2] if len(r) > 2 else None,
        "d4": r[4] if len(r) > 4 else None,
        "d13": r[13] if len(r) > 13 else None,
        "d14": r[14] if len(r) > 14 else None,
    }


stubs()

from sundance_spa import (  # noqa: E402
    BTN_BLOWER,
    BTN_CLEARRAY,
    BTN_PUMP1,
    BTN_PUMP2,
    CC_REQ,
    CC_REQ_ALT,
    SpaClient,
)


async def press(client: SpaClient, btn: int, mtype: int, label: str) -> dict:
    before = snap(client.status)
    await client.send_button(btn, mtype)
    await client.wait_status(4, 5.0)
    after = snap(client.status)
    entry = {"btn": btn, "mtype": mtype, "label": label, "before": before, "after": after}
    log("press", entry)
    return after


async def main() -> None:
    client = SpaClient("192.168.178.54", 8899)
    await client.connect()
    await client.wait_ready(12)
    log("start", snap(client.status))

    # Wasserfall aus (239) – solange circ_manual an
    for i in range(3):
        st = client.status
        if st and st["circ_manual"]:
            await press(client, BTN_CLEARRAY, CC_REQ, f"waterfall_off_{i}")
            await asyncio.sleep(2)
        else:
            break

    # Pumpen sicherheitshalber aus
    for _ in range(2):
        st = client.status
        if not st:
            break
        if st["pump1"]:
            await press(client, BTN_PUMP1, CC_REQ, "pump1_off")
            await asyncio.sleep(2)
        if st["pump2"]:
            await press(client, BTN_PUMP2, CC_REQ, "pump2_off")
            await asyncio.sleep(2)

    # Blubber-Kandidaten aus (243, 236, 240-247)
    blower_candidates = [
        (BTN_BLOWER, CC_REQ_ALT, "243_17"),
        (BTN_BLOWER, CC_REQ, "243_cc"),
        (236, CC_REQ, "236_cc"),
        (240, CC_REQ, "240_cc"),
        (241, CC_REQ, "241_cc"),
        (242, CC_REQ, "242_cc"),
        (244, CC_REQ, "244_cc"),
        (245, CC_REQ, "245_cc"),
    ]
    for btn, mtype, label in blower_candidates:
        await press(client, btn, mtype, f"blower_try_{label}")
        await asyncio.sleep(2)

    log("end", snap(client.status))
    await client.disconnect()
    print("Alles-aus-Sequenz fertig.")


if __name__ == "__main__":
    asyncio.run(main())
