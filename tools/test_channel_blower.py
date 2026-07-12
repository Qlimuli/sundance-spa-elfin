"""Test commands on assigned RS485 channel vs 0x10."""
from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "custom_components"))


def _install_ha_stubs() -> None:
    ha = types.ModuleType("homeassistant")
    config_entries = types.ModuleType("homeassistant.config_entries")
    config_entries.ConfigEntry = type("ConfigEntry", (), {})
    const = types.ModuleType("homeassistant.const")
    const.CONF_HOST, const.CONF_PORT = "host", "port"
    const.Platform = type("Platform", (), {
        "CLIMATE": "climate", "SWITCH": "switch", "LIGHT": "light", "SENSOR": "sensor"
    })
    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = type("HomeAssistant", (), {})
    coordinator_mod = types.ModuleType("homeassistant.helpers.update_coordinator")
    coordinator_mod.UpdateFailed = type("UpdateFailed", (Exception,), {})
    coordinator_mod.DataUpdateCoordinator = type("DataUpdateCoordinator", (), {
        "__init__": lambda self, *a, **k: None
    })
    sys.modules.update({
        "homeassistant": ha,
        "homeassistant.config_entries": config_entries,
        "homeassistant.const": const,
        "homeassistant.core": core,
        "homeassistant.helpers": types.ModuleType("homeassistant.helpers"),
        "homeassistant.helpers.update_coordinator": coordinator_mod,
    })


_install_ha_stubs()

from sundance_spa import BTN_BLOWER, BTN_PUMP1, CC_REQ_ALT, CMD_CHANNEL, SpaClient, _build_cc  # noqa: E402


async def send_on_channel(client: SpaClient, btn: int, ch: int, mtype: int = 0xCC) -> None:
    async with client._pending_lock:
        client._pending.append((ch, _build_cc(btn, ch, mtype)))
    await asyncio.sleep(3)


async def main() -> None:
    client = SpaClient("192.168.178.54", 8899)
    await client.connect()
    await client.wait_ready(12)
    ach = client._assigned_channel
    print(f"Assigned channel: 0x{ach:02X}" if ach else "none")
    s0 = client.status
    print("Before:", s0 and {"p1": s0["pump1"], "p2": s0["pump2"], "raw4": s0["raw"][4]})

    print("\n--- pump1 on ASSIGNED channel ---")
    await send_on_channel(client, BTN_PUMP1, ach or CMD_CHANNEL)
    await client.wait_status(4, 5)
    s1 = client.status
    print("After p1 assigned:", s1 and {"p1": s1["pump1"], "raw4": s1["raw"][4]})

    print("\n--- blower 243 CC on ASSIGNED channel ---")
    await send_on_channel(client, BTN_BLOWER, ach or CMD_CHANNEL, 0xCC)
    await asyncio.sleep(3)

    print("\n--- blower 243 0x17 on ASSIGNED channel ---")
    await send_on_channel(client, BTN_BLOWER, ach or CMD_CHANNEL, CC_REQ_ALT)
    await asyncio.sleep(3)

    print("\n--- blower 243 CC on 0x10 ---")
    await send_on_channel(client, BTN_BLOWER, CMD_CHANNEL, 0xCC)
    await asyncio.sleep(3)

    await client.disconnect()
    print("Fertig – hörst du Pumpe/Blubber?")


if __name__ == "__main__":
    asyncio.run(main())
