"""
Sundance / Balboa Spa – Home Assistant Integration
Protokoll-Engine + DataUpdateCoordinator in einer Datei.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

_LOGGER = logging.getLogger(__name__)

DOMAIN = "sundance_spa"
PLATFORMS = [Platform.CLIMATE, Platform.SWITCH, Platform.LIGHT, Platform.SENSOR]

# ── Protokoll-Konstanten ─────────────────────────────────────────────────────
M_STARTEND        = 0x7E
CLEAR_TO_SEND     = 0x06
STATUS_UPDATE     = 0xC4
LIGHTS_UPDATE     = 0xCA
STATUS_UPDATE_ALT = 0x16
LIGHTS_UPDATE_ALT = 0x23
CC_REQ            = 0xCC
CMD_CHANNEL       = 0x10
CH_BROADCAST      = 0xFE
MSG_CHANNEL_REQ   = 0x01
MSG_CHANNEL_ASSIGN = 0x02
MSG_NACK          = 0x00
MSG_SET_TEMP      = 0xC6
CLIENT_TYPE_PANEL = 0x02

# ── Button-Codes ─────────────────────────────────────────────────────────────
BTN_PUMP1       = 228
BTN_PUMP2       = 229
BTN_CLEARRAY    = 239
BTN_LIGHT       = 241
BTN_LIGHT_COLOR = 242
BTN_ZIRK        = 242
BTN_BLOWER      = 243

# ── Lookup-Tabellen ──────────────────────────────────────────────────────────
HEAT_MODE_MAP = {32: "AUTO", 34: "ECO", 36: "DAY"}

DISPLAY_MAP = {
    22: "Solltemp-Änderung",
    23: "Ist-Temperatur",
    30: "Solltemperatur",
    31: "Ist-Temperatur (idle)",
    32: "Ist-Temperatur",
    36: "Ist-Temperatur",
    35: "Primärfiltration",
    42: "Heizmodus",
     3: "Einstellungs-Menü",
     0: "Temperatureinheit",
}

LIGHT_MODE_MAP = {
    128: "Fast Blend", 127: "Slow Blend", 255: "Frozen Blend",
      2: "Blue",  7: "Violet", 6: "Red",   8: "Amber",
      3: "Green", 9: "Aqua",   1: "White", 0: "Off",
}

DISPLAY_TEMP_OK = {22, 23, 30, 31, 32, 36}


# ── Protokoll-Hilfsfunktionen ────────────────────────────────────────────────

def _calc_cs(data: bytes | bytearray, length: int) -> int:
    crc = 0xB5
    for cur in range(length):
        for i in range(8):
            bit = crc & 0x80
            crc = ((crc << 1) & 0xFF) | ((data[cur] >> (7 - i)) & 0x01)
            if bit:
                crc ^= 0x07
        crc &= 0xFF
    for i in range(8):
        bit = crc & 0x80
        crc = (crc << 1) & 0xFF
        if bit:
            crc ^= 0x07
    return (crc ^ 0x02) & 0xFF


def _xormsg(data: bytes | bytearray) -> list[int]:
    result = []
    for i in range(0, len(data) - 1, 2):
        result.append(data[i] ^ data[i + 1] ^ 1)
    return result


def _build_cc(btn: int, channel: int = CMD_CHANNEL) -> bytes:
    ml  = 7
    msg = bytearray(9)
    msg[0] = M_STARTEND
    msg[1] = ml
    msg[2] = channel
    msg[3] = 0xBF
    msg[4] = CC_REQ
    msg[5] = btn & 0xFF
    msg[6] = 0
    msg[7] = _calc_cs(msg[1:ml], ml - 1)
    msg[8] = M_STARTEND
    return bytes(msg)


def _build_channel_request() -> bytes:
    msg = bytearray(8)
    msg[0] = M_STARTEND
    msg[1] = 6
    msg[2] = CH_BROADCAST
    msg[3] = 0xBF
    msg[4] = MSG_CHANNEL_REQ
    msg[5] = CLIENT_TYPE_PANEL
    msg[6] = _calc_cs(msg[1:6], 5)
    msg[7] = M_STARTEND
    return bytes(msg)


def _build_nack(channel: int) -> bytes:
    msg = bytearray(8)
    msg[0] = M_STARTEND
    msg[1] = 6
    msg[2] = channel
    msg[3] = 0xBF
    msg[4] = MSG_NACK
    msg[5] = 0x00
    msg[6] = _calc_cs(msg[1:6], 5)
    msg[7] = M_STARTEND
    return bytes(msg)


def _build_c6_temp(target_temp: float, channel: int) -> bytes:
    """Verbessertes C6-Paket für Sundance Cameo 880."""
    target_temp = max(20.0, min(40.0, round(target_temp * 2) / 2.0))
    raw_temp = int(round(target_temp * 2)) & 0xFF

    msg = bytearray(9)
    msg[0] = M_STARTEND
    msg[1] = 7
    msg[2] = channel
    msg[3] = 0xBF
    msg[4] = MSG_SET_TEMP
    msg[5] = raw_temp
    msg[6] = 0x00
    msg[7] = _calc_cs(msg[1:7], 6)
    msg[8] = M_STARTEND
    return bytes(msg)


def _decode_c4(raw: bytes) -> dict | None:
    d = _xormsg(raw[5:len(raw) - 2])
    if len(d) < 15:
        return None
    circ = (d[1] >> 6) & 1
    return {
        "time":         f"{d[0] ^ 6:02d}:{d[11]:02d}",
        "cur_temp":     (d[5] ^ 2) / 2.0 if (d[5] ^ 2) != 255 else None,
        "set_temp":     d[8] / 2.0,
        "heat_active":  bool((d[10] >> 6) & 1),
        "heat_mode":    HEAT_MODE_MAP.get(d[6], f"0x{d[6]:02X}"),
        "pump1":        bool((d[2] >> 4) & 1),
        "pump2":        bool((d[1] >> 2) & 1),
        "circ":         bool(circ),
        "circ_manual":  bool((d[1] >> 7) & 1),
        "circ_running": bool((d[1] >> 5) & 1),
        "blower":       bool((d[13] >> 2) & 0x03),
        "display_val":  d[13],
        "display":      DISPLAY_MAP.get(d[13], f"Code {d[13]}"),
        "in_menu":      d[13] not in DISPLAY_TEMP_OK,
        "raw_d8":       d[8],
        "raw":          list(d),
    }


def _decode_ca(raw: bytes) -> dict | None:
    d = _xormsg(raw[5:len(raw) - 2])
    if len(d) < 10:
        return None
    return {
        "on":             d[1] > 0,
        "brightness":     round(d[1] / 2.55),
        "brightness_raw": d[1],
        "mode":           LIGHT_MODE_MAP.get(d[4], f"0x{d[4]:02X}"),
        "mode_raw":       d[4],
        "r": d[8], "g": d[6], "b": d[2],
        "hs_color":       _rgb_to_hs(d[8], d[6], d[2]),
        "raw":            list(d),
    }


def _rgb_to_hs(r: int, g: int, b: int) -> tuple[float, float]:
    r_, g_, b_ = r / 255.0, g / 255.0, b / 255.0
    cmax  = max(r_, g_, b_)
    cmin  = min(r_, g_, b_)
    delta = cmax - cmin
    if delta == 0:
        h = 0.0
    elif cmax == r_:
        h = 60 * (((g_ - b_) / delta) % 6)
    elif cmax == g_:
        h = 60 * (((b_ - r_) / delta) + 2)
    else:
        h = 60 * (((r_ - g_) / delta) + 4)
    s = 0.0 if cmax == 0 else (delta / cmax) * 100
    return round(h, 1), round(s, 1)


# ── SpaClient ────────────────────────────────────────────────────────────────

class SpaClient:
    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._send_q: asyncio.Queue = asyncio.Queue()
        self._recv_task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._status: dict | None = None
        self._lights:  dict | None = None
        self._status_seq = 0
        self._lights_seq  = 0
        self._cts_ch = 0
        self._connected = False
        self._lock = asyncio.Lock()
        self._cmd_lock = asyncio.Lock()
        self._assigned_channel: int | None = None
        self._channel_assigned = asyncio.Event()

    async def connect(self) -> None:
        self._reader, self._writer = await asyncio.open_connection(self.host, self.port)
        import socket as _s
        sock = self._writer.transport.get_extra_info("socket")
        if sock:
            sock.setsockopt(_s.IPPROTO_TCP, _s.TCP_NODELAY, 1)
        self._stop.clear()
        self._connected = True
        self._assigned_channel = None
        self._channel_assigned = asyncio.Event()
        self._recv_task = asyncio.create_task(self._receiver())
        await self._assign_channel()
        _LOGGER.info("Spa verbunden: %s:%s (Kanal 0x%02X)", self.host, self.port, self._assigned_channel or 0)

    async def disconnect(self) -> None:
        self._connected = False
        self._stop.set()
        if self._recv_task:
            self._recv_task.cancel()
            try:
                await self._recv_task
            except asyncio.CancelledError:
                pass
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass

    async def _read_msg(self) -> bytes | None:
        assert self._reader is not None
        hf, rlen = False, 0
        while not hf or rlen == 0:
            try:
                b = await asyncio.wait_for(self._reader.readexactly(1), timeout=15.0)
            except Exception:
                return None
            if b[0] == M_STARTEND:
                hf = True
            elif hf:
                rlen = b[0]
        if rlen > 128:
            return None
        try:
            rest = await asyncio.wait_for(self._reader.readexactly(rlen), timeout=5.0)
        except Exception:
            return None
        full = bytes([M_STARTEND, rlen]) + rest
        if _calc_cs(full[1:], rlen - 1) != full[-2]:
            return None
        return full

    async def _receiver(self) -> None:
        assert self._writer is not None
        while not self._stop.is_set():
            msg = await self._read_msg()
            if msg is None:
                continue
            if len(msg) < 5:
                continue
            mtype   = msg[4]
            channel = msg[2]

            if mtype == MSG_CHANNEL_ASSIGN and len(msg) >= 7:
                assigned = msg[5]
                self._assigned_channel = assigned
                self._channel_assigned.set()
                _LOGGER.info("Spa-Kanal zugewiesen: 0x%02X", assigned)
                continue

            if mtype == CLEAR_TO_SEND:
                assigned = self._assigned_channel or CMD_CHANNEL
                if channel == CMD_CHANNEL or channel == assigned:
                    self._cts_ch += 1
                    if not self._send_q.empty():
                        try:
                            pkt = self._send_q.get_nowait()
                            self._writer.write(pkt)
                            await self._writer.drain()
                        except Exception as exc:
                            _LOGGER.debug("TX-Fehler: %s", exc)
                continue

            if mtype in (STATUS_UPDATE, STATUS_UPDATE_ALT):
                dec = _decode_c4(msg)
                if dec:
                    async with self._lock:
                        self._status = dec
                        self._status_seq += 1

            elif mtype in (LIGHTS_UPDATE, LIGHTS_UPDATE_ALT):
                dec = _decode_ca(msg)
                if dec:
                    async with self._lock:
                        self._lights = dec
                        self._lights_seq += 1

    async def _write_direct(self, packet: bytes) -> None:
        if not self._writer:
            raise UpdateFailed("Keine Verbindung zum Spa")
        self._writer.write(packet)
        await self._writer.drain()

    async def _assign_channel(self) -> None:
        await asyncio.sleep(0.5)
        await self._write_direct(_build_channel_request())
        try:
            await asyncio.wait_for(self._channel_assigned.wait(), timeout=8.0)
        except asyncio.TimeoutError:
            self._assigned_channel = CMD_CHANNEL
            _LOGGER.warning("Kein Channel-Assignment – Fallback auf 0x%02X", CMD_CHANNEL)
        ch = self._assigned_channel or CMD_CHANNEL
        self._assigned_channel = ch
        await self._write_direct(_build_nack(ch))
        await asyncio.sleep(0.3)

    async def send_button(self, btn: int) -> None:
        await self._send_q.put(_build_cc(btn))

    async def wait_status(self, n: int = 6, timeout: float = 4.0) -> bool:
        start = self._status_seq
        elapsed = 0.0
        while elapsed < timeout:
            await asyncio.sleep(0.1)
            elapsed += 0.1
            if self._status_seq >= start + n:
                return True
        return False

    async def wait_ready(self, timeout: float = 10.0) -> bool:
        elapsed = 0.0
        while elapsed < timeout:
            if self._status:
                return True
            await asyncio.sleep(0.2)
            elapsed += 0.2
        return False

    async def _status_snapshot(self) -> dict | None:
        async with self._lock:
            return dict(self._status) if self._status else None

    async def _ensure_channel(self) -> int:
        if self._assigned_channel is not None:
            return self._assigned_channel
        await self._assign_channel()
        return self._assigned_channel or CMD_CHANNEL

    async def set_temperature(self, target: float) -> None:
        """Verbesserte Temperatur-Steuerung für Sundance Cameo 880."""
        target = max(20.0, min(40.0, round(target * 2) / 2.0))
        raw_target = int(round(target * 2))

        async with self._cmd_lock:
            channel = await self._ensure_channel()
            snap = await self._status_snapshot()
            if not snap:
                raise UpdateFailed("Kein Status vom Spa")

            if abs(snap["set_temp"] - target) < 0.3:
                return

            _LOGGER.info("C6 Setze Temperatur auf %.1f°C (raw=0x%02X)", target, raw_target)

            for attempt in range(5):
                pkt = _build_c6_temp(target, channel)
                await self._send_q.put(pkt)
                await self.wait_status(n=8, timeout=8.0)

                deadline = time.monotonic() + 10.0
                while time.monotonic() < deadline:
                    cur = await self._status_snapshot()
                    if cur and abs(cur["set_temp"] - target) < 0.4:
                        _LOGGER.info("✅ Temperatur erfolgreich auf %.1f°C gesetzt", cur["set_temp"])
                        return
                    await asyncio.sleep(0.2)

                _LOGGER.warning("C6 Versuch %d fehlgeschlagen", attempt + 1)
                await self._write_direct(_build_nack(channel))
                await asyncio.sleep(0.5)

            raise UpdateFailed(f"Temperatur konnte nicht auf {target:.1f}°C gesetzt werden (bleibt bei 41°C)")


# ── DataUpdateCoordinator ────────────────────────────────────────────────────

class SpaCoordinator(DataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, client: SpaClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=5),
        )
        self.client = client

    async def _async_update_data(self) -> dict:
        if not self.client.is_connected:
            raise UpdateFailed("Keine Verbindung zum Spa")
        s = self.client.status
        l = self.client.lights
        if s is None:
            raise UpdateFailed("Noch keine Daten vom Spa")
        return {"status": s, "lights": l}


# ── Setup / Teardown ─────────────────────────────────────────────────────────

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    host = entry.data[CONF_HOST]
    port = entry.data.get(CONF_PORT, 8899)

    client = SpaClient(host, port)
    try:
        await client.connect()
        await client.wait_ready(timeout=12.0)
    except Exception as exc:
        _LOGGER.error("Verbindung zu Spa fehlgeschlagen: %s", exc)
        raise

    coordinator = SpaCoordinator(hass, client)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "client":      client,
        "coordinator": coordinator,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        data = hass.data[DOMAIN].pop(entry.entry_id)
        await data["client"].disconnect()
    return unload_ok
