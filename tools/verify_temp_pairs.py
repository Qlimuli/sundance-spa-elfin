"""Verify encrypted temp pairs direction."""
from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "custom_components"))


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


stubs()
from sundance_spa import CC_REQ, SpaClient  # noqa: E402


async def press(c: SpaClient, btn: int, b6: int, label: str) -> None:
    s = c.status
    b, r = s["set_temp"], s["raw_d8"]
    await c.send_button(btn, CC_REQ, b6)
    await c.wait_status(8, 6.0)
    a = c.status
    print(
        label,
        f"btn={btn} b6={b6} dec={btn ^ b6 ^ 1}:",
        f"{b}({r}) -> {a['set_temp']}({a['raw_d8']})",
        f"delta={a['set_temp'] - b}",
    )


async def main() -> None:
    c = SpaClient("192.168.178.54", 8899)
    await c.connect()
    await c.wait_ready(12)
    print("start", c.status["set_temp"])
    await press(c, 18, 242, "pair_225")
    await press(c, 0, 227, "pair_226")
    await press(c, 18, 242, "pair_225_again")
    await c.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
