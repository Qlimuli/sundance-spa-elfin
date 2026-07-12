"""Direkttest Blubber (btn 243, mtype 0x17) mit SpaClient."""
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

    class ConfigEntry:
        pass

    config_entries.ConfigEntry = ConfigEntry
    const = types.ModuleType("homeassistant.const")
    const.CONF_HOST = "host"
    const.CONF_PORT = "port"

    class Platform:
        CLIMATE = "climate"
        SWITCH = "switch"
        LIGHT = "light"
        SENSOR = "sensor"

    const.Platform = Platform
    core = types.ModuleType("homeassistant.core")

    class HomeAssistant:
        pass

    core.HomeAssistant = HomeAssistant
    coordinator_mod = types.ModuleType("homeassistant.helpers.update_coordinator")

    class UpdateFailed(Exception):
        pass

    class DataUpdateCoordinator:
        def __init__(self, *args, **kwargs) -> None:
            pass

    coordinator_mod.UpdateFailed = UpdateFailed
    coordinator_mod.DataUpdateCoordinator = DataUpdateCoordinator

    sys.modules["homeassistant"] = ha
    sys.modules["homeassistant.config_entries"] = config_entries
    sys.modules["homeassistant.const"] = const
    sys.modules["homeassistant.core"] = core
    sys.modules["homeassistant.helpers"] = types.ModuleType("homeassistant.helpers")
    sys.modules["homeassistant.helpers.update_coordinator"] = coordinator_mod


_install_ha_stubs()

from sundance_spa import BTN_BLOWER, CC_REQ_ALT, SpaClient  # noqa: E402


async def main() -> None:
    host, port = "192.168.178.54", 8899
    client = SpaClient(host, port)
    print(f"Verbinde mit {host}:{port} …")
    await client.connect()
    await client.wait_ready(12)
    print("Kanal:", hex(client.assigned_channel or 0))

    print("--- Blubber EIN (Toggle 1) ---")
    await client.send_button(BTN_BLOWER, CC_REQ_ALT)
    await asyncio.sleep(8)

    print("--- Blubber AUS (Toggle 2) ---")
    await client.send_button(BTN_BLOWER, CC_REQ_ALT)
    await asyncio.sleep(5)

    await client.disconnect()
    print("Fertig – hörst du den Blubber?")


if __name__ == "__main__":
    asyncio.run(main())
