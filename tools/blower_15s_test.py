"""Blubber 15s: 243/0x17, dann Pumpe2+243/0x17."""
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
        "sessionId": "b00787", "runId": "blower-15s", "hypothesisId": "H18",
        "message": msg, "data": data, "timestamp": int(time.time() * 1000),
    }, ensure_ascii=False)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    print(f">>> {msg}: {data}")


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


async def press(client: SpaClient, btn: int, mtype: int = CC_REQ) -> None:
    await client.send_button(btn, mtype)
    await client.wait_status(4, 5.0)


async def all_off(client: SpaClient) -> None:
    for _ in range(2):
        s = client.status
        if not s:
            return
        if s["circ_manual"]:
            await press(client, BTN_CLEARRAY)
            await asyncio.sleep(2)
        if s["pump2"]:
            await press(client, BTN_PUMP2)
            await asyncio.sleep(2)
        if s["pump1"]:
            await press(client, BTN_PUMP1)
            await asyncio.sleep(2)


async def cycle(client: SpaClient, name: str, prep: list[tuple[int, int]], off: list[tuple[int, int]]) -> None:
    log(f"{name}_prep", {"steps": prep})
    for btn, mtype in prep:
        await press(client, btn, mtype)
        await asyncio.sleep(2)
    log(f"{name}_on", {"seconds": 15})
    print(f"\n=== {name}: BLUBBER AN (15 Sek.) ===\n")
    await asyncio.sleep(15)
    log(f"{name}_off", {"steps": off})
    print(f"\n=== {name}: BLUBBER AUS ===\n")
    for btn, mtype in off:
        await press(client, btn, mtype)
        await asyncio.sleep(2)


async def main() -> None:
    client = SpaClient("192.168.178.54", 8899)
    await client.connect()
    await client.wait_ready(12)
    log("start", {"status": client.status is not None})

    await all_off(client)
    await asyncio.sleep(2)

    # Test 1: nur 243 / 0x17
    await cycle(
        client,
        "243_only",
        [(BTN_BLOWER, CC_REQ_ALT)],
        [(BTN_BLOWER, CC_REQ_ALT)],
    )
    await all_off(client)
    await asyncio.sleep(4)

    # Test 2: Pumpe2 an, dann 243 / 0x17 (Zustand aus Hunt-Scan)
    await cycle(
        client,
        "p2_243",
        [(BTN_PUMP2, CC_REQ), (BTN_BLOWER, CC_REQ_ALT)],
        [(BTN_BLOWER, CC_REQ_ALT), (BTN_PUMP2, CC_REQ)],
    )

    await client.disconnect()
    log("done", {})
    print("Fertig – hörst du bei Test 1 oder Test 2 den Blubber?")


if __name__ == "__main__":
    asyncio.run(main())
