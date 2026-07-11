"""Live-Test der neuen Feedback-Logik ohne Home Assistant."""
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

    class ConfigEntry:  # noqa: D106
        pass

    config_entries.ConfigEntry = ConfigEntry
    const = types.ModuleType("homeassistant.const")
    const.CONF_HOST = "host"
    const.CONF_PORT = "port"

    class Platform:  # noqa: D106
        CLIMATE = "climate"
        SWITCH = "switch"
        LIGHT = "light"
        SENSOR = "sensor"

    const.Platform = Platform
    core = types.ModuleType("homeassistant.core")

    class HomeAssistant:  # noqa: D106
        pass

    core.HomeAssistant = HomeAssistant
    coordinator_mod = types.ModuleType("homeassistant.helpers.update_coordinator")

    class UpdateFailed(Exception):
        pass

    class DataUpdateCoordinator:  # noqa: D106
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

from sundance_spa import SpaClient  # type: ignore  # noqa: E402


async def main() -> None:
    client = SpaClient("192.168.178.54", 8899)
    await client.connect()
    await client.wait_ready(12)
    print("Status:", client.status)
    print("Lights:", client.lights)
    print("Channel:", hex(client.assigned_channel or 0))

    try:
        print("--- set temp 30 ---")
        await client.set_temperature(30.0)
        print("New status:", client.status)
    except Exception as exc:
        print("Temp failed:", exc)

    try:
        print("--- light on ---")
        await client.set_light(on=True)
        print("New lights:", client.lights)
    except Exception as exc:
        print("Light failed:", exc)

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
