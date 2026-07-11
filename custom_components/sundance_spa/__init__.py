"""
Sundance / Balboa Spa – Home Assistant Integration
Protokoll-Engine + DataUpdateCoordinator in einer Datei.
"""
from __future__ import annotations

import asyncio
import logging
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
MSG_CHANNEL_REQ    = 0x01
MSG_CHANNEL_ASSIGN = 0x02
MSG_CHANNEL_ACK    = 0x03
CLIENT_CLEAR_TO_SEND = 0x00
CLIENT_TYPE_PANEL  = 0x02
CC_REQ_ALT         = 0x17

DETECT_CHANNEL_CYCLES = 5
CHECKS_BEFORE_RETRY   = 2
NO_CHANGE_REQUESTED   = -1.0
LIGHT_NO_CHANGE       = -1

# ── Button-Codes ─────────────────────────────────────────────────────────────
BTN_TEMP_UP        = 225
BTN_TEMP_DOWN      = 226
BTN_TEMP_RANGE_LOW = 200
BTN_TEMP_RANGE_HI  = 201
BTN_PUMP1          = 228
BTN_PUMP2          = 229
BTN_CLEARRAY       = 239
BTN_LIGHT          = 241
BTN_LIGHT_COLOR    = 242
BTN_ZIRK           = 242
BTN_BLOWER         = 243

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

LIGHT_MODE_BY_NAME = {name: code for code, name in LIGHT_MODE_MAP.items()}

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


def _build_cc(btn: int, channel: int = CMD_CHANNEL, mtype: int = CC_REQ) -> bytes:
    ml  = 7
    msg = bytearray(9)
    msg[0] = M_STARTEND
    msg[1] = ml
    msg[2] = channel
    msg[3] = 0xBF
    msg[4] = mtype
    msg[5] = btn & 0xFF
    msg[6] = 0
    msg[7] = _calc_cs(msg[1:ml], ml - 1)
    msg[8] = M_STARTEND
    return bytes(msg)


def _build_channel_request() -> bytes:
    """Channel-Assignment auf Broadcast 0xFE (Sundance / Balboa RS485)."""
    msg = bytearray(10)
    msg[0] = M_STARTEND
    msg[1] = 8
    msg[2] = CH_BROADCAST
    msg[3] = 0xBF
    msg[4] = MSG_CHANNEL_REQ
    msg[5] = CLIENT_TYPE_PANEL
    msg[6] = 0xF1
    msg[7] = 0x73
    msg[8] = _calc_cs(msg[1:8], 7)
    msg[9] = M_STARTEND
    return bytes(msg)


def _build_channel_ack(channel: int) -> bytes:
    """Kanal-Zuweisung bestätigen (0x03)."""
    msg = bytearray(7)
    msg[0] = M_STARTEND
    msg[1] = 5
    msg[2] = channel
    msg[3] = 0xBF
    msg[4] = MSG_CHANNEL_ACK
    msg[5] = _calc_cs(msg[1:5], 4)
    msg[6] = M_STARTEND
    return bytes(msg)


def _decode_set_temp(raw: int, _celsius_scale: bool) -> float:
    """Soll-Temperatur dekodieren (Cameo 880: niedrige Werte = °C×2, hohe = °F)."""
    if raw >= 80:
        return round((raw - 32) * 5 / 9, 1)
    return raw / 2.0


def _brightness_step(level_pct: int) -> int:
    """Spa-Helligkeitsstufen (0 / 33 / 66 / 100)."""
    if level_pct <= 0:
        return 0
    if level_pct < 50:
        return 33
    if level_pct < 83:
        return 66
    return 100


def _decode_c4(raw: bytes) -> dict | None:
    d = _xormsg(raw[5:len(raw) - 2])
    if len(d) < 15:
        return None
    circ = (d[1] >> 6) & 1
    celsius_scale = bool(d[9] & 0x01) if len(d) > 9 else True
    set_raw = d[8]
    return {
        "time":         f"{d[0] ^ 6:02d}:{d[11]:02d}",
        "cur_temp":     (d[5] ^ 2) / 2.0 if (d[5] ^ 2) != 255 else None,
        "set_temp":     _decode_set_temp(set_raw, celsius_scale),
        "heat_active":  bool((d[10] >> 6) & 1),
        "heat_mode":    HEAT_MODE_MAP.get(d[6], f"0x{d[6]:02X}"),
        "pump1":        bool((d[2] >> 4) & 1),
        "pump2":        bool((d[1] >> 2) & 1),
        "circ":         bool(circ),
        "circ_manual":  bool((d[1] >> 7) & 1),
        "circ_running": bool((d[1] >> 5) & 1),
        "blower":       False,
        "display_val":  d[13],
        "display":      DISPLAY_MAP.get(d[13], f"Code {d[13]}"),
        "in_menu":      d[13] not in DISPLAY_TEMP_OK,
        "celsius_scale": celsius_scale,
        "raw_d8":       set_raw,
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
    """Minimal RGB → (Hue 0-360, Saturation 0-100) ohne externe Libs."""
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


# ── SpaClient: TCP-Verbindung & Sende-Queue ──────────────────────────────────

class SpaClient:
    """Verwaltet die TCP-Verbindung zum Spa-Controller."""

    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._pending: list[tuple[int, bytes]] = []
        self._pending_lock = asyncio.Lock()
        self._recv_task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._status: dict | None = None
        self._lights: dict | None = None
        self._status_seq = 0
        self._lights_seq = 0
        self._connected = False
        self._lock = asyncio.Lock()
        self._cmd_lock = asyncio.Lock()
        self._assigned_channel: int | None = None
        self._channel_ready = asyncio.Event()
        self._discovered_channels: list[int] = []
        self._active_channels: list[int] = []
        self._detect_state = 0
        self._target_temp = NO_CHANGE_REQUESTED
        self._temp_done = asyncio.Event()
        self._temp_check = 0
        self._target_light_brightness = LIGHT_NO_CHANGE
        self._target_light_mode = LIGHT_NO_CHANGE
        self._light_done = asyncio.Event()
        self._light_check = 0

    async def connect(self) -> None:
        self._reader, self._writer = await asyncio.open_connection(
            self.host, self.port
        )
        import socket as _s

        sock = self._writer.transport.get_extra_info("socket")
        if sock:
            sock.setsockopt(_s.IPPROTO_TCP, _s.TCP_NODELAY, 1)
        self._stop.clear()
        self._connected = True
        self._reset_channel_state()
        self._recv_task = asyncio.create_task(self._receiver())
        await self._write_direct(_build_channel_request())
        try:
            await asyncio.wait_for(self._channel_ready.wait(), timeout=12.0)
        except asyncio.TimeoutError:
            self._assigned_channel = CMD_CHANNEL
            self._channel_ready.set()
            _LOGGER.warning(
                "Kein Channel-Assignment – Fallback auf 0x%02X", CMD_CHANNEL
            )
        _LOGGER.info(
            "Spa verbunden: %s:%s (Kanal 0x%02X)",
            self.host,
            self.port,
            self._assigned_channel or 0,
        )

    def _reset_channel_state(self) -> None:
        self._assigned_channel = None
        self._channel_ready = asyncio.Event()
        self._discovered_channels = []
        self._active_channels = []
        self._detect_state = 0
        self._pending = []

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
            if msg is None or len(msg) < 5:
                continue
            mtype = msg[4]
            channel = msg[2]

            if mtype == MSG_CHANNEL_ASSIGN and len(msg) >= 7:
                assigned = msg[5]
                self._assigned_channel = assigned
                self._channel_ready.set()
                _LOGGER.info("Spa-Kanal zugewiesen: 0x%02X", assigned)
                await self._write_direct(_build_channel_ack(assigned))
                continue

            if (
                mtype == CLIENT_CLEAR_TO_SEND
                and self._assigned_channel is None
                and self._detect_state >= DETECT_CHANNEL_CYCLES
            ):
                await self._write_direct(_build_channel_request())

            if mtype == CLEAR_TO_SEND:
                if channel not in self._discovered_channels:
                    self._discovered_channels.append(channel)
                await self._flush_pending(channel)
                if self._detect_state < DETECT_CHANNEL_CYCLES:
                    self._detect_state += 1
                if (
                    self._assigned_channel is None
                    and self._detect_state >= DETECT_CHANNEL_CYCLES
                ):
                    self._pick_idle_channel()
                continue

            if mtype in (CC_REQ, CC_REQ_ALT) and channel not in self._active_channels:
                self._active_channels.append(channel)

            if mtype in (STATUS_UPDATE, STATUS_UPDATE_ALT):
                dec = _decode_c4(msg)
                if dec:
                    async with self._lock:
                        self._status = dec
                        self._status_seq += 1
                    await self._handle_temp_feedback(dec)

            elif mtype in (LIGHTS_UPDATE, LIGHTS_UPDATE_ALT):
                dec = _decode_ca(msg)
                if dec:
                    async with self._lock:
                        self._lights = dec
                        self._lights_seq += 1
                    await self._handle_light_feedback(dec)

    def _pick_idle_channel(self) -> None:
        for ch in sorted(self._discovered_channels):
            if ch not in self._active_channels:
                self._assigned_channel = ch
                self._channel_ready.set()
                _LOGGER.info("Freien Bus-Kanal gewählt: 0x%02X", ch)
                return

    async def _flush_pending(self, channel: int) -> None:
        assert self._writer is not None
        async with self._pending_lock:
            for idx, (pkt_ch, pkt) in enumerate(self._pending):
                if pkt_ch == channel:
                    self._writer.write(pkt)
                    await self._writer.drain()
                    self._pending.pop(idx)
                    return

    async def _write_direct(self, packet: bytes) -> None:
        if not self._writer:
            raise UpdateFailed("Keine Verbindung zum Spa")
        self._writer.write(packet)
        await self._writer.drain()

    async def _queue_cc(self, btn: int, mtype: int = CC_REQ) -> None:
        await self._ensure_channel()
        # Sundance Cameo / Balboa: CTS auf 0x10, Pumpen funktionieren dort zuverlässig.
        ch = CMD_CHANNEL
        async with self._pending_lock:
            self._pending.append((ch, _build_cc(btn, ch, mtype)))

    async def _handle_temp_feedback(self, status: dict) -> None:
        if self._temp_check > 0:
            self._temp_check -= 1
        if self._temp_check > 0 or self._target_temp == NO_CHANGE_REQUESTED:
            return

        current = status["set_temp"]
        if abs(current - self._target_temp) < 0.3:
            self._target_temp = NO_CHANGE_REQUESTED
            self._temp_done.set()
            _LOGGER.info("Soll-Temperatur erreicht: %.1f °C", current)
            return

        btn = BTN_TEMP_DOWN if self._target_temp < current else BTN_TEMP_UP
        await self._queue_cc(btn)
        self._temp_check = CHECKS_BEFORE_RETRY

    async def _handle_light_feedback(self, lights: dict) -> None:
        if self._light_check > 0:
            self._light_check -= 1
        if self._light_check > 0:
            return

        if self._target_light_mode != LIGHT_NO_CHANGE:
            if lights["mode_raw"] == self._target_light_mode:
                self._target_light_mode = LIGHT_NO_CHANGE
                self._light_done.set()
            elif lights["brightness_raw"] == 0:
                await self._queue_cc(BTN_LIGHT)
                self._light_check = CHECKS_BEFORE_RETRY
            else:
                await self._queue_cc(BTN_LIGHT_COLOR)
                self._light_check = CHECKS_BEFORE_RETRY
            return

        if self._target_light_brightness == LIGHT_NO_CHANGE:
            return

        if lights["brightness_raw"] == self._target_light_brightness:
            self._target_light_brightness = LIGHT_NO_CHANGE
            self._light_done.set()
            return

        await self._queue_cc(BTN_LIGHT)
        self._light_check = CHECKS_BEFORE_RETRY

    async def send_button(self, btn: int, mtype: int = CC_REQ) -> None:
        await self._queue_cc(btn, mtype)

    async def wait_status(self, n: int = 6, timeout: float = 4.0) -> bool:
        start = self._status_seq
        elapsed = 0.0
        while elapsed < timeout:
            await asyncio.sleep(0.1)
            elapsed += 0.1
            if self._status_seq >= start + n:
                return True
        return False

    async def wait_lights(self, n: int = 3, timeout: float = 4.0) -> bool:
        start = self._lights_seq
        elapsed = 0.0
        while elapsed < timeout:
            await asyncio.sleep(0.1)
            elapsed += 0.1
            if self._lights_seq >= start + n:
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

    @property
    def status(self) -> dict | None:
        return self._status

    @property
    def lights(self) -> dict | None:
        return self._lights

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def assigned_channel(self) -> int | None:
        return self._assigned_channel

    async def _status_snapshot(self) -> dict | None:
        async with self._lock:
            return dict(self._status) if self._status else None

    async def _lights_snapshot(self) -> dict | None:
        async with self._lock:
            return dict(self._lights) if self._lights else None

    async def _ensure_channel(self) -> int:
        if self._assigned_channel is not None:
            return self._assigned_channel
        try:
            await asyncio.wait_for(self._channel_ready.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            self._assigned_channel = CMD_CHANNEL
        return self._assigned_channel or CMD_CHANNEL

    async def _ensure_temp_range(self, target: float, current_raw: int) -> None:
        """Cameo 880: Temperaturbereich (Low/High) vor Feineinstellung umschalten."""
        high_range = current_raw >= 80
        want_high = target >= 37.0
        if high_range != want_high:
            await self._queue_cc(BTN_TEMP_RANGE_HI if want_high else BTN_TEMP_RANGE_LOW)
            self._temp_check = CHECKS_BEFORE_RETRY

    async def _ensure_pumps_off_for_heating(self) -> None:
        """Cameo 880 (40A): Temperaturänderung nur bei ausgeschalteten Jet-Pumpen."""
        for _ in range(6):
            snap = await self._status_snapshot()
            if not snap:
                return
            if not snap["pump1"] and not snap["pump2"]:
                return
            if snap["pump1"]:
                await self._queue_cc(BTN_PUMP1)
            if snap["pump2"]:
                await self._queue_cc(BTN_PUMP2)
            await asyncio.sleep(1.5)

    async def set_temperature(self, target: float) -> None:
        """Soll-Temperatur per Warmer/Cooler-Tasten (Feedback-Schleife)."""
        target = max(20.0, min(40.0, target))
        async with self._cmd_lock:
            snap = await self._status_snapshot()
            if not snap:
                raise UpdateFailed("Kein Status vom Spa")
            if abs(snap["set_temp"] - target) < 0.3:
                return

            await self._ensure_channel()
            await self._ensure_pumps_off_for_heating()
            await self._ensure_temp_range(target, snap["raw_d8"])
            self._temp_done.clear()
            self._temp_check = 0
            self._target_temp = target
            _LOGGER.debug(
                "Ziel-Temperatur %.1f °C (aktuell %.1f °C, raw=%s)",
                target,
                snap["set_temp"],
                snap["raw_d8"],
            )
            await self._handle_temp_feedback(snap)

            try:
                await asyncio.wait_for(self._temp_done.wait(), timeout=120.0)
            except asyncio.TimeoutError as exc:
                final = await self._status_snapshot()
                got = final["set_temp"] if final else None
                raise UpdateFailed(
                    f"Soll-Temperatur konnte nicht auf {target:.1f} °C gesetzt werden "
                    f"(aktuell: {got} °C). Prüfen Sie ggf. die Temperatur-Sperre am Panel."
                ) from exc
            finally:
                self._target_temp = NO_CHANGE_REQUESTED

    async def set_light(
        self,
        *,
        on: bool | None = None,
        brightness_pct: int | None = None,
        effect: str | None = None,
    ) -> None:
        """Licht steuern mit Retry/Feedback wie im Sundance-RS485-Referenzprojekt."""
        async with self._cmd_lock:
            await self._ensure_channel()
            self._light_done.clear()
            self._light_check = 0
            self._target_light_mode = LIGHT_NO_CHANGE
            self._target_light_brightness = LIGHT_NO_CHANGE

            if effect is not None:
                mode = LIGHT_MODE_BY_NAME.get(effect)
                if mode is None:
                    raise UpdateFailed(f"Unbekannter Licht-Effekt: {effect}")
                lights = await self._lights_snapshot()
                if not lights or lights["brightness_raw"] == 0:
                    self._target_light_brightness = 100
                    try:
                        await asyncio.wait_for(self._light_done.wait(), timeout=30.0)
                    except asyncio.TimeoutError:
                        raise UpdateFailed("Licht konnte nicht eingeschaltet werden")
                    self._light_done.clear()
                    self._light_check = 0
                self._target_light_mode = mode
            elif on is False or (brightness_pct is not None and brightness_pct <= 0):
                self._target_light_brightness = 0
            elif brightness_pct is not None:
                self._target_light_brightness = _brightness_step(brightness_pct)
            elif on is True:
                self._target_light_brightness = 100
            else:
                return

            lights = await self._lights_snapshot()
            if lights:
                await self._handle_light_feedback(lights)

            try:
                await asyncio.wait_for(self._light_done.wait(), timeout=60.0)
            except asyncio.TimeoutError as exc:
                lights = await self._lights_snapshot()
                state = "an" if lights and lights.get("on") else "aus"
                raise UpdateFailed(f"Licht-Zielzustand nicht erreicht (aktuell {state})") from exc
            finally:
                self._target_light_brightness = LIGHT_NO_CHANGE
                self._target_light_mode = LIGHT_NO_CHANGE


# ── DataUpdateCoordinator ────────────────────────────────────────────────────

class SpaCoordinator(DataUpdateCoordinator):
    """Koordiniert Daten-Updates und hält den SpaClient am Leben."""

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
