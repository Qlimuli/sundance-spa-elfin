"""
Sundance / Balboa Spa – Home Assistant Integration
Protokoll-Engine + DataUpdateCoordinator in einer Datei.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import timedelta
from pathlib import Path

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

_LOGGER = logging.getLogger(__name__)

# #region agent log
def _agent_debug_log(
    location: str, message: str, data: dict, hypothesis_id: str, run_id: str = "temp-debug",
) -> None:
    payload = {
        "sessionId": "b00787", "runId": run_id, "hypothesisId": hypothesis_id,
        "location": location, "message": message, "data": data,
        "timestamp": int(time.time() * 1000),
    }
    line = json.dumps(payload, ensure_ascii=False) + "\n"
    here = Path(__file__).resolve().parent
    for log_path in (here / "debug-b00787.log", here.parents[2] / "debug-b00787.log"):
        try:
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write(line)
        except OSError:
            pass
# #endregion

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
C6_REQ            = 0xC6  # Cameo iTouch Temp/UI (Panel-Sniff 2026-08-15)
CMD_CHANNEL       = 0x10
CH_BROADCAST      = 0xFE
MSG_CHANNEL_REQ    = 0x01
MSG_CHANNEL_ASSIGN = 0x02
MSG_CHANNEL_ACK    = 0x03
MSG_EXISTING_CLIENT_REQ  = 0x04
MSG_EXISTING_CLIENT_RESP = 0x05
CLIENT_CLEAR_TO_SEND = 0x00
CLIENT_TYPE_PANEL  = 0x02
CC_REQ_ALT         = 0x17

DETECT_CHANNEL_CYCLES = 5
CHECKS_BEFORE_RETRY   = 2   # Status-Pakete zwischen Temp-Schritten
TEMP_STEP_MIN_S       = 0.5 # Mindestabstand – Status braucht Zeit zum Setzen
NO_CHANGE_REQUESTED   = -1.0
LIGHT_NO_CHANGE       = -1
MAX_COMMAND_ATTEMPTS  = 80  # harte Obergrenze (kein Bus-Spam)
MAX_PENDING_CC        = 1   # nur 1 Befehl auf dem Bus – schont Panel
PENDING_WAIT_S        = 4.0 # max. Warten bis CTS den Slot freigibt

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
# Nur CC-Lichtcodes – KEINE C6 (C6-Replays haben Solltemp auf ~40°C getrieben)
LIGHT_ON_VARIANTS: tuple[tuple[int, int, int], ...] = (
    (CC_REQ, 241, 0),
    (CC_REQ, 0xF1, 0x00),
)
LIGHT_COLOR_VARIANTS: tuple[tuple[int, int, int], ...] = (
    (CC_REQ, 242, 0),
    (CC_REQ, 0xF2, 0x00),
)
BTN_ZIRK           = 242
BTN_BLOWER         = 237   # Klartext NICHT verwenden – steuert Pumpen; siehe BLOWER_CC_*

# ── Lookup-Tabellen ──────────────────────────────────────────────────────────
HEAT_MODE_MAP = {32: "AUTO", 34: "ECO", 36: "DAY"}

DISPLAY_MAP = {
    22: "Solltemp-Änderung",
    23: "Ist-Temperatur",
    30: "Solltemperatur",
    31: "Ist-Temperatur (idle)",
    32: "Ist-Temperatur",
    36: "Ist-Temperatur",
     8: "Cameo Home/Idle",
    35: "Primärfiltration",
    42: "Heizmodus",
    47: "Sekundärfiltration",
    48: "UV-Intervall",
    51: "Wasserwechsel",
    53: "Filterwechsel",
    59: "Datum",
    62: "Uhrzeit",
    14: "Panel-/Temp-Sperre",
     3: "Einstellungs-Menü",
     0: "Temperatureinheit",
}

LIGHT_MODE_MAP = {
    128: "Fast Blend", 127: "Slow Blend", 255: "Frozen Blend",
      2: "Blue",  7: "Violet", 6: "Red",   8: "Amber",
      3: "Green", 9: "Aqua",   1: "White", 0: "Off",
}

LIGHT_MODE_BY_NAME = {name: code for code, name in LIGHT_MODE_MAP.items()}

# Cameo: Code 8 ist oft Normalzustand. Nur bekannte Menüs blockieren.
DISPLAY_TEMP_OK = {8, 22, 23, 30, 31, 32, 36}
DISPLAY_MENU_CODES = {0, 3, 14, 35, 42, 47, 48, 51, 53, 59, 62}


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




def _jacuzzi_xor_cipher(packet: bytearray, encrypt: bool = True) -> bytearray:
    """Jacuzzi/Sundance XOR-Cipher (dhmsjs/jacuzzi_decrypt).

    Symmetric – encrypt und decrypt sind identisch.
    Unterstützt C4/CA/CC. Für C6 experimentell key1=packet[5]^0xC6.
    """
    if len(packet) < 7:
        return packet
    packet = bytearray(packet)
    packet_type = packet[4]
    if packet_type == 0xC4:
        key1 = packet[5] ^ 0x19
    elif packet_type == 0xCA:
        key1 = packet[5] ^ 0x59
    elif packet_type == 0xCC:
        key1 = packet[5] ^ 0xDF
    elif packet_type == 0xC6:
        key1 = packet[5] ^ 0xC6  # experimentell
    else:
        return packet

    HEADER_LENGTH = 5
    packet_length = packet[1]
    key2 = packet_length - HEADER_LENGTH - 2
    for i in range(6, packet_length):
        key2 = (key2 - 1) % 64
        packet[i] = packet[i] ^ key1 ^ key2

    if not encrypt:
        # Nach Decrypt: Extra-Key-Byte auf 0 setzen
        packet[5] = 0
        packet[-2] = _calc_cs(packet[1:packet_length], packet_length - 1)
    else:
        # Nach Encrypt: Checksum neu
        packet[-2] = _calc_cs(packet[1:packet_length], packet_length - 1)
    return packet


def _build_cc_encrypted(
    btn: int,
    channel: int = CMD_CHANNEL,
    mtype: int = CC_REQ,
    b6: int = 0,
    key_byte: int = 0,
) -> bytes:
    """Verschlüsseltes CC (Länge 8, Extra-Key-Byte) für Cameo/encrypted Boards."""
    ml = 8
    msg = bytearray(10)
    msg[0] = M_STARTEND
    msg[1] = ml
    msg[2] = channel & 0xFF
    msg[3] = 0xBF
    msg[4] = mtype
    msg[5] = key_byte & 0xFF
    msg[6] = btn & 0xFF
    msg[7] = b6 & 0xFF
    msg[8] = 0  # CS placeholder
    msg[9] = M_STARTEND
    msg = _jacuzzi_xor_cipher(msg, encrypt=True)
    return bytes(msg)


def _build_c6_encrypted(
    btn: int,
    b6: int,
    channel: int = CMD_CHANNEL,
    key_byte: int = 0,
) -> bytes:
    """C6 mit optionalem Jacuzzi-Cipher (experimentell)."""
    ml = 8
    msg = bytearray(10)
    msg[0] = M_STARTEND
    msg[1] = ml
    msg[2] = channel & 0xFF
    msg[3] = 0xBF
    msg[4] = 0xC6
    msg[5] = key_byte & 0xFF
    msg[6] = btn & 0xFF
    msg[7] = b6 & 0xFF
    msg[8] = 0
    msg[9] = M_STARTEND
    msg = _jacuzzi_xor_cipher(msg, encrypt=True)
    return bytes(msg)

def _xormsg(data: bytes | bytearray) -> list[int]:
    result = []
    for i in range(0, len(data) - 1, 2):
        result.append(data[i] ^ data[i + 1] ^ 1)
    return result


def _build_cc(
    btn: int,
    channel: int = CMD_CHANNEL,
    mtype: int = CC_REQ,
    b6: int = 0,
) -> bytes:
    ml  = 7
    msg = bytearray(9)
    msg[0] = M_STARTEND
    msg[1] = ml
    msg[2] = channel
    msg[3] = 0xBF
    msg[4] = mtype
    msg[5] = btn & 0xFF
    msg[6] = b6 & 0xFF
    msg[7] = _calc_cs(msg[1:ml], ml - 1)
    msg[8] = M_STARTEND
    return bytes(msg)


# Cameo 880: Blubber = verschlüsseltes Panel-CC (53/217). Klartext 237 schaltet Pumpen!
BLOWER_CC_BTN = 53
BLOWER_CC_B6  = 217
BLOWER_CC_ALT: tuple[tuple[int, int], ...] = ((204, 32),)

# Log 14:48 Ziel 28.5 (DOWN) – eindeutige Zuordnung:
#   EC 59, C8 7C  →  DOWN (31→30.5→30.0)
#   52 E7, 49 FF  →  UP   (30→30.5→31.0)  – NIEMALS bei DOWN senden!
# Format: (mtype, btn, b6)
# Sichere Codes + Variation gegen Debounce identischer Frames
# UP: F0 47 = C6-Replay (Log-bewährt); 225/0 = CC Klartext TEMP_UP (HyperActiveJ)
TEMP_UP_CODES: list[tuple[int, int, int]] = [
    (0xC6, 0xF0, 0x47),  # Log: konsistent UP (auch +2.5)
]
TEMP_DOWN_CODES: list[tuple[int, int, int]] = [
    (0xC6, 0xEC, 0x59),
    (0xC6, 0xC8, 0x7C),
]
MSG_SET_TEMP = 0x20
TEMP_UP_C6 = tuple((b, x) for m, b, x in TEMP_UP_CODES if m == 0xC6)
TEMP_DOWN_C6 = tuple((b, x) for m, b, x in TEMP_DOWN_CODES if m == 0xC6)
# Fallback 0xCC (780/HyperActiveJ) – falls C6 nicht greift
TEMP_UP_VARIANTS: tuple[tuple[int, int], ...] = (
    (BTN_TEMP_UP, 0),
    (18, 242),
    (19, 243),
)
TEMP_DOWN_VARIANTS: tuple[tuple[int, int], ...] = (
    (BTN_TEMP_DOWN, 0),
    (0, 227),
)
# Panel-Sniff: Range High = 141/69 (decoded 201)
TEMP_RANGE_HI_CC_BTN = 141
TEMP_RANGE_HI_CC_B6  = 69
BTN_MENU = 254

# Verifiziert aus Log 10:37 DOWN 30→28 Erfolg:
SEED_DOWN_C6: list[tuple[int, int, int]] = [
    (0xC6, 0x15, 0xA5),
    (0xC6, 0x74, 0xC5),
    (0xC6, 0x8E, 0x3C),
    (0xC6, 0x98, 0x2B),
    (0xC6, 0xF1, 0x45),
]
SEED_UP_C6: list[tuple[int, int, int]] = [
    (0xC6, 0xF0, 0x47),
    (0xC6, 0x52, 0xE7),
]



def _build_set_temp(channel: int, celsius: float) -> bytes:
    """Direkt-Solltemperatur (Msg 0x20). Wert = °C × 2 (halbe Grad)."""
    val = int(round(celsius * 2.0))
    val = max(40, min(80, val))  # 20.0 … 40.0 °C
    ml = 6
    msg = bytearray(8)
    msg[0] = M_STARTEND
    msg[1] = ml
    msg[2] = channel & 0xFF
    msg[3] = 0xBF
    msg[4] = MSG_SET_TEMP
    msg[5] = val & 0xFF
    msg[6] = _calc_cs(msg[1:ml], ml - 1)
    msg[7] = M_STARTEND
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


def _build_existing_client_resp(channel: int) -> bytes:
    """Antwort auf Existing-Client-Request (0x05) – hält Kanalzuweisung am Leben."""
    msg = bytearray(10)
    msg[0] = M_STARTEND
    msg[1] = 8
    msg[2] = channel
    msg[3] = 0xBF
    msg[4] = MSG_EXISTING_CLIENT_RESP
    msg[5] = 0x04
    msg[6] = 0x08
    msg[7] = 0x00
    msg[8] = _calc_cs(msg[1:8], 7)
    msg[9] = M_STARTEND
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
        "blower":       False,  # kein zuverlässiges RS485-Feld bekannt (≠ Pumpe 1)
        "display_val":  d[13],
        "display":      DISPLAY_MAP.get(d[13], f"Code {d[13]}"),
        "display_code": d[13],
        # Nur bekannte Menü-Screens blockieren – Idle/unbekannt erlaubt Temp
        "in_menu":      d[13] in DISPLAY_MENU_CODES,
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
        self._command_attempts = 0
        self._cc_queued = 0
        self._cc_sent = 0
        self._cts_own = 0
        self._last_cc_hex: str | None = None
        self._last_temp_seen: float | None = None
        self._temp_start: float | None = None
        self._temp_no_progress = 0
        self._last_temp_cmd_ts = 0.0
        self._temp_up_idx: int | None = None
        self._temp_down_idx: int | None = None
        self._last_temp_idx: int | None = None
        self._last_temp_warmer: bool = True
        self._temp_blacklist_up: set = set()
        self._temp_blacklist_down: set = set()
        self._last_temp_code: tuple | None = None
        self._temp_steps_done = 0
        self._temp_fail_on_code = 0
        self._temp_skip_code = None
        self._temp_stall_rounds = 0
        self._temp_code_idx = 0
        self._temp_same_code_retries = 0
        self._last_rx_ts = 0.0
        self._last_status_ts = 0.0
        self._learned_light: list[tuple[int, int, int]] = []  # (mtype, btn, b6)
        self._light_attempt = 0
        self._debug_cmd = False
        self._recent_tx: set = set()
        self._learned_c6_up: list = list(SEED_UP_C6)
        self._learned_c6_down: list = list(SEED_DOWN_C6)
        self._panel_c6_recent: list = []  # (ts, btn, b6)
        self._last_set_temp_seen: float | None = None
        self._temp_locked_code = None
        self._c6_score: dict = {}
        self._temp_send_mode = 0
        self._sniff_panel_cc = True
        self._last_logged_display: int | None = None
        self._last_logged_set: float | None = None
        self._sniff_panel_cc = True  # Panel-Tasten (fremde Kanäle) immer loggen
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
        # Channel-Discovery läuft im Receiver (CTS-Zyklen + ggf. Assignment-Request).
        # Kein sofortiger Broadcast-Request – analog zur Referenzimplementierung.
        try:
            await asyncio.wait_for(self._channel_ready.wait(), timeout=15.0)
        except asyncio.TimeoutError:
            self._assigned_channel = CMD_CHANNEL  # Cameo: Panel + wir auf 0x10
            self._channel_ready.set()
            _LOGGER.warning("Channel-Timeout – Fallback 0x%02X", CMD_CHANNEL)
        # Cameo iTouch: C6/Befehle nur auf 0x10 wirksam – Kanal erzwingen
        self._assigned_channel = CMD_CHANNEL
        self._channel_ready.set()
        self._last_rx_ts = time.monotonic()
        _LOGGER.info(
            "Spa verbunden: %s:%s – Channel assigned: 0x%02X",
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

            # ── Channel Assignment Response (0x02) ──────────────────────────
            if mtype == MSG_CHANNEL_ASSIGN and len(msg) >= 7:
                assigned = msg[5]
                self._assigned_channel = assigned
                self._channel_ready.set()
                _LOGGER.info("Channel assigned: 0x%02X (via Assignment-Response)", assigned)
                await self._write_direct(_build_channel_ack(assigned))
                continue

            # ── Existing-Client Request (0x04) – Board fragt nach alten Clients
            if mtype == MSG_EXISTING_CLIENT_REQ and self._assigned_channel is not None:
                await self._write_direct(
                    _build_existing_client_resp(self._assigned_channel)
                )
                continue

            # ── Client Clear-To-Send (0x00) → Channel-Request senden ─────────
            if (
                mtype == CLIENT_CLEAR_TO_SEND
                and self._assigned_channel is None
                and self._detect_state >= DETECT_CHANNEL_CYCLES
            ):
                _LOGGER.debug("CLIENT_CTS ohne Kanal – sende Channel-Request")
                await self._write_direct(_build_channel_request())
                continue

            # ── Clear-To-Send (0x06) – einziges Fenster zum Senden ───────────
            if mtype == CLEAR_TO_SEND:
                if channel not in self._discovered_channels:
                    self._discovered_channels.append(channel)
                    _LOGGER.debug(
                        "CTS entdeckt auf Kanal 0x%02X (discovered=%s)",
                        channel,
                        [f"0x{c:02X}" for c in self._discovered_channels],
                    )

                if self._assigned_channel is not None and channel == self._assigned_channel:
                    self._cts_own += 1
                    await self._flush_pending(channel)
                elif (
                    self._assigned_channel is not None
                    and self._pending
                    and len(self._discovered_channels) <= 2
                ):
                    # Cameo: manchmal CTS auf 0x11 während wir 0x10 nutzen
                    await self._flush_pending(self._assigned_channel)

                # Idle-Kanal wählen, falls noch kein Assignment
                if self._detect_state < DETECT_CHANNEL_CYCLES:
                    self._detect_state += 1
                if (
                    self._assigned_channel is None
                    and self._detect_state >= DETECT_CHANNEL_CYCLES
                ):
                    self._pick_idle_channel()
                continue

            # ── Andere Geräte aktiv (CC-Traffic) ────────────────────────────
            if mtype in (CC_REQ, CC_REQ_ALT):
                if channel not in self._active_channels:
                    self._active_channels.append(channel)
                    _LOGGER.info(
                        "Aktiver Bus-Kanal erkannt: 0x%02X (active=%s)",
                        channel,
                        [f"0x{c:02X}" for c in self._active_channels],
                    )
                if self._detect_state < DETECT_CHANNEL_CYCLES:
                    self._detect_state += 1
                    if (
                        self._detect_state >= DETECT_CHANNEL_CYCLES
                        and self._assigned_channel is None
                    ):
                        self._pick_idle_channel()

                # Panel-Sniff: jedes CC das nicht unser Echo ist (auch ch 0x10)
                if self._sniff_panel_cc and len(msg) >= 7:
                    btn_b5 = msg[5]
                    b6 = msg[6] if len(msg) > 6 else 0
                    decoded = btn_b5 ^ b6 ^ 1
                    raw_hex = msg.hex(" ")
                    is_echo = (
                        self._last_cc_hex is not None
                        and raw_hex == self._last_cc_hex
                    )
                    if not is_echo:
                        # Fremd = nicht in unseren letzten TX (auch gleicher Kanal!)
                        recent = getattr(self, "_recent_tx", set())
                        is_ours = raw_hex in recent
                        src = "EIGEN" if is_ours else "PANEL"
                        if src == "PANEL":
                            if channel not in self._active_channels:
                                self._active_channels.append(channel)
                            entry = (mtype, btn_b5, b6)
                            # Temp-Codes lernen
                            if not hasattr(self, "_learned_temp_up"):
                                self._learned_temp_up = []
                                self._learned_temp_down = []
                            _LOGGER.warning(
                                "PANEL-SNIFF CC | ch=0x%02X mtype=0x%02X "
                                "btn=0x%02X b6=0x%02X dec=%d raw=%s",
                                channel, mtype, btn_b5, b6, decoded, raw_hex,
                            )
                        else:
                            _LOGGER.debug(
                                "CC-Echo EIGEN | raw=%s", raw_hex,
                            )

            # Unbekannte Message-Typen (Touch-Panel kann anderes nutzen)
            elif (
                self._sniff_panel_cc
                and mtype
                not in (
                    CLEAR_TO_SEND,
                    CLIENT_CLEAR_TO_SEND,
                    MSG_CHANNEL_ASSIGN,
                    MSG_EXISTING_CLIENT_REQ,
                    STATUS_UPDATE,
                    STATUS_UPDATE_ALT,
                    LIGHTS_UPDATE,
                    LIGHTS_UPDATE_ALT,
                    CC_REQ,
                    CC_REQ_ALT,
                    0x07,  # NOTHING_TO_SEND
                )
                and len(msg) >= 5
            ):
                if mtype == C6_REQ and len(msg) >= 7:
                    raw_hex = msg.hex(" ")
                    recent = getattr(self, "_recent_tx", set())
                    if raw_hex not in recent:
                        # Roh-Bytes
                        if len(msg) >= 9 and msg[1] >= 8:
                            # ggf. encrypted (extra key byte)
                            dec = _jacuzzi_xor_cipher(bytearray(msg), encrypt=False)
                            _LOGGER.warning(
                                "PANEL-SNIFF C6-ENC | raw=%s decrypted=%s "
                                "btn=0x%02X b6=0x%02X",
                                raw_hex, bytes(dec).hex(" "),
                                dec[6] if len(dec) > 6 else 0,
                                dec[7] if len(dec) > 7 else 0,
                            )
                            btn, b6 = msg[6], msg[7] if len(msg) > 7 else 0
                        else:
                            btn, b6 = msg[5], msg[6] if len(msg) > 6 else 0
                            _LOGGER.warning(
                                "PANEL-SNIFF C6 | ch=0x%02X btn=0x%02X b6=0x%02X "
                                "xor=0x%02X sum=0x%02X raw=%s",
                                channel, btn, b6, btn ^ b6, (btn + b6) & 0xFF, raw_hex,
                            )
                        self._panel_c6_recent.append((time.monotonic(), btn, b6))
                        if len(self._panel_c6_recent) > 80:
                            self._panel_c6_recent = self._panel_c6_recent[-60:]
                elif self._debug_cmd:
                    _LOGGER.debug(
                        "BUS-MSG unbekannt | ch=0x%02X mtype=0x%02X raw=%s",
                        channel,
                        mtype,
                        msg.hex(" "),
                    )

            # ── Status / Lights ─────────────────────────────────────────────
            if mtype in (STATUS_UPDATE, STATUS_UPDATE_ALT):
                dec = _decode_c4(msg)
                if dec:
                    async with self._lock:
                        self._status = dec
                        self._status_seq += 1
                        self._last_status_ts = time.monotonic()
                    if self._debug_cmd:
                        dcode = dec.get("display_code")
                        st = dec.get("set_temp")
                        if (
                            dcode != self._last_logged_display
                            or st != self._last_logged_set
                        ):
                            self._last_logged_display = dcode
                            self._last_logged_set = st
                            _LOGGER.info(
                                "STATUS | set=%.1f raw_d8=%s cur=%s display=%s "
                                "code=%s in_menu=%s heat=%s p1=%s p2=%s",
                                st if st is not None else -1,
                                dec.get("raw_d8"),
                                dec.get("cur_temp"),
                                dec.get("display"),
                                dcode,
                                dec.get("in_menu"),
                                dec.get("heat_active"),
                                dec.get("pump1"),
                                dec.get("pump2"),
                            )
                    self._last_status = dec
                    if dec.get("set_temp") is not None:
                        self._learn_c6_from_settemp(float(dec["set_temp"]))
                    await self._handle_temp_feedback(dec)

            elif mtype in (LIGHTS_UPDATE, LIGHTS_UPDATE_ALT):
                dec = _decode_ca(msg)
                if dec:
                    async with self._lock:
                        self._lights = dec
                        self._lights_seq += 1
                    if self._debug_cmd:
                        _LOGGER.info(
                            "LIGHTS | on=%s bright=%s mode=%s mode_raw=%s "
                            "rgb=(%s,%s,%s)",
                            dec.get("on"),
                            dec.get("brightness_raw"),
                            dec.get("mode"),
                            dec.get("mode_raw"),
                            dec.get("r"),
                            dec.get("g"),
                            dec.get("b"),
                        )
                    await self._handle_light_feedback(dec)

    def _pick_idle_channel(self) -> None:
        """Wählt CTS-Kanal. 0x10 ist OK wenn kein anderer existiert (Cameo oft nur 0x10)."""
        avoid = set(self._active_channels)
        candidates = sorted(
            ch for ch in self._discovered_channels if ch not in avoid
        )
        if not candidates:
            candidates = sorted(self._discovered_channels)
        if candidates:
            # Bevorzuge nicht-0x10, aber nimm 0x10 wenn es der einzige ist
            preferred = [c for c in candidates if c != 0x10] or candidates
            ch = preferred[0]
            self._assigned_channel = ch
            self._channel_ready.set()
            _LOGGER.info(
                "Channel assigned: 0x%02X (discovered=%s active=%s)",
                ch,
                [f"0x{c:02X}" for c in self._discovered_channels],
                [f"0x{c:02X}" for c in self._active_channels],
            )
            return
        # Letzter Fallback – still und ohne Spam
        if self._assigned_channel is None:
            self._assigned_channel = CMD_CHANNEL
            self._channel_ready.set()
            _LOGGER.info("Channel Fallback: 0x%02X", CMD_CHANNEL)

    async def _flush_pending(self, channel: int) -> None:
        """Sendet ein wartendes Paket auf CTS-Kanal (oder beliebig wenn Solo-Kanal)."""
        assert self._writer is not None
        async with self._pending_lock:
            if not self._pending:
                return
            # Primär exakter Kanal, sonst erstes Paket (Cameo oft nur 1 Kanal)
            idx_send = None
            for idx, (pkt_ch, pkt) in enumerate(self._pending):
                if pkt_ch == channel:
                    idx_send = idx
                    break
            if idx_send is None and len(self._discovered_channels) <= 1:
                idx_send = 0
            if idx_send is None:
                return
            pkt_ch, pkt = self._pending.pop(idx_send)
            self._writer.write(pkt)
            await self._writer.drain()
            self._cc_sent += 1
            self._last_cc_hex = pkt.hex(" ")
            if not hasattr(self, "_recent_tx"):
                self._recent_tx = set()
            self._recent_tx.add(self._last_cc_hex)
            if len(self._recent_tx) > 40:
                self._recent_tx = set(list(self._recent_tx)[-20:])
            _LOGGER.warning(
                "TX GESENDET ch=0x%02X (pkt_ch=0x%02X) sent=%d: %s",
                channel, pkt_ch, self._cc_sent, self._last_cc_hex,
            )

    async def _write_direct(self, packet: bytes) -> None:
        if not self._writer:
            raise UpdateFailed("Keine Verbindung zum Spa")
        self._writer.write(packet)
        await self._writer.drain()

    async def _wait_pending_clear(self, timeout: float = PENDING_WAIT_S) -> bool:
        """Wartet, bis die Pending-Queue leer ist (CTS hat gesendet) oder Timeout."""
        elapsed = 0.0
        while elapsed < timeout:
            async with self._pending_lock:
                if not self._pending:
                    return True
            await asyncio.sleep(0.1)
            elapsed += 0.1
        async with self._pending_lock:
            left = len(self._pending)
        if left:
            _LOGGER.warning(
                "Pending-CC nach %.1fs noch nicht gesendet (%d wartend, cts_own=%d)",
                timeout,
                left,
                self._cts_own,
            )
        return left == 0

    async def _queue_cc(self, btn: int, mtype: int = CC_REQ, b6: int = 0) -> None:
        """Reiht einen Tastenbefehl ein – wartet ggf. auf freien Slot, droppt nicht sofort."""
        ch = await self._ensure_channel()
        pkt = _build_cc(btn, ch, mtype, b6)

        # Slot freimachen: auf CTS warten statt ältere Befehle zu verwerfen
        waited = 0.0
        while waited < PENDING_WAIT_S:
            async with self._pending_lock:
                if len(self._pending) < MAX_PENDING_CC:
                    self._pending.append((ch, pkt))
                    pending_n = len(self._pending)
                    break
            await asyncio.sleep(0.1)
            waited += 0.1
        else:
            async with self._pending_lock:
                if len(self._pending) >= MAX_PENDING_CC:
                    dropped = self._pending.pop(0)
                    _LOGGER.warning(
                        "Pending-CC verworfen nach %.1fs Wartezeit: %s",
                        PENDING_WAIT_S,
                        dropped[1].hex(" "),
                    )
                self._pending.append((ch, pkt))
                pending_n = len(self._pending)

        self._cc_queued += 1
        _LOGGER.info(
            "CC eingeplant: btn=%d b6=%d dec=%d kanal=0x%02X pending=%d "
            "queued_total=%d sent_total=%d cts_own=%d paket=%s",
            btn,
            b6,
            btn ^ b6 ^ 1,
            ch,
            pending_n,
            self._cc_queued,
            self._cc_sent,
            self._cts_own,
            pkt.hex(" "),
        )


    async def _queue_raw(self, pkt: bytes) -> None:
        """Reiht ein fertiges Paket ein (z.B. Direct-Set 0x20)."""
        ch = await self._ensure_channel()
        # Kanal im Paket ggf. anpassen (Byte 2)
        if len(pkt) > 2:
            ba = bytearray(pkt)
            ba[2] = ch & 0xFF
            # Checksum neu (ml = ba[1], cs an Position ml)
            ml = ba[1]
            if len(ba) > ml:
                ba[ml] = _calc_cs(ba[1:ml], ml - 1)
            pkt = bytes(ba)
        waited = 0.0
        while waited < PENDING_WAIT_S:
            async with self._pending_lock:
                if len(self._pending) < MAX_PENDING_CC:
                    self._pending.append((ch, pkt))
                    self._cc_queued += 1
                    _LOGGER.warning(
                        "RAW eingeplant ch=0x%02X: %s", ch, pkt.hex(" ")
                    )
                    return
            await asyncio.sleep(0.1)
            waited += 0.1
        async with self._pending_lock:
            if len(self._pending) < MAX_PENDING_CC:
                self._pending.append((ch, pkt))
                self._cc_queued += 1

    async def send_blower_toggle(self) -> None:
        """Blubber ein/aus – verschlüsseltes Panel-CC (53/217)."""
        await self._queue_cc(BLOWER_CC_BTN, CC_REQ, BLOWER_CC_B6)


    def _learn_c6_from_settemp(self, new_set: float) -> None:
        """Panel-C6 der letzten 1,2s der Soll-Änderung zuordnen (nur wenn wir nicht senden)."""
        prev = self._last_set_temp_seen
        self._last_set_temp_seen = new_set
        if prev is None:
            return
        delta = new_set - prev
        if abs(delta) < 0.25:
            return
        # Während eigener Steuerung nicht vom Panel lernen (sonst Selbst-Feedback)
        if self._target_temp != NO_CHANGE_REQUESTED:
            return
        now = time.monotonic()
        recent = [
            (b, x) for (ts, b, x) in self._panel_c6_recent
            if now - ts < 1.2
        ]
        if not recent:
            return
        btn, b6 = recent[-1]
        entry = (0xC6, btn, b6)
        if not hasattr(self, "_c6_score"):
            self._c6_score = {}
        if delta > 0:
            if entry not in self._learned_c6_up:
                self._learned_c6_up.append(entry)
            self._learned_c6_down = [c for c in self._learned_c6_down if c != entry]
            self._c6_score[entry] = self._c6_score.get(entry, 0) + 1
            _LOGGER.warning(
                "LEARN-UP C6 btn=0x%02X b6=0x%02X (%.1f→%.1f) pool=%d",
                btn, b6, prev, new_set, len(self._learned_c6_up),
            )
        else:
            if entry not in self._learned_c6_down:
                self._learned_c6_down.append(entry)
            self._learned_c6_up = [c for c in self._learned_c6_up if c != entry]
            self._c6_score[entry] = self._c6_score.get(entry, 0) + 1
            _LOGGER.warning(
                "LEARN-DOWN C6 btn=0x%02X b6=0x%02X (%.1f→%.1f) pool=%d",
                btn, b6, prev, new_set, len(self._learned_c6_down),
            )


    async def _send_temp_step(self, warmer: bool) -> None:
        """Nur gelernte C6 mit positivem Score; sonst FIXED-C6 Fallback."""
        if self._assigned_channel is None:
            self._assigned_channel = CMD_CHANNEL
        ch = self._assigned_channel

        if self._command_attempts > 100:
            _LOGGER.error("Temp: >100 Versuche – Abbruch")
            self._target_temp = NO_CHANGE_REQUESTED
            async with self._pending_lock:
                self._pending.clear()
            self._temp_done.set()
            return

        await self._wait_pending_clear(timeout=3.0)
        scores = getattr(self, "_c6_score", {})

        if warmer:
            pool = list(self._learned_c6_up)
            fixed = [(0xC6, 0xF0, 0x47), (0xC6, 0x52, 0xE7)]
            label = "TEMP_UP"
        else:
            pool = list(self._learned_c6_down)
            fixed = [(0xC6, 0xEC, 0x59), (0xC6, 0xC8, 0x7C), (0xC6, 0x15, 0xA5),
                     (0xC6, 0x74, 0xC5), (0xC6, 0x8E, 0x3C), (0xC6, 0x98, 0x2B)]
            label = "TEMP_DOWN"

        # Codes mit Score > 0 bevorzugen; neutrale (0) erlauben; negative raus
        good = [c for c in pool if scores.get(c, 0) >= 0]
        # Sort: höchster Score zuerst, dann rotieren
        good.sort(key=lambda c: scores.get(c, 0), reverse=True)
        codes = good if good else fixed

        idx = int(getattr(self, "_temp_code_idx", 0)) % len(codes)
        mtype, btn, b6 = codes[idx]
        pkt = _build_cc(btn, ch, mtype, b6)

        _LOGGER.warning(
            "Temp-Schritt %s btn=0x%02X b6=0x%02X score=%s idx=%d/%d attempt=%d pool=%d",
            label, btn, b6, scores.get((mtype, btn, b6), 0),
            idx, len(codes), self._command_attempts + 1, len(pool),
        )
        self._last_temp_code = (mtype, btn, b6)
        self._last_temp_warmer = warmer
        await self._queue_raw(pkt)
        self._last_temp_cmd_ts = time.monotonic()
        self._command_attempts += 1
        self._temp_code_idx = idx + 1


    async def _handle_temp_feedback(self, status: dict) -> None:
        """Score gelernte Codes; bei Fortschritt Pause; bei GEGEN aus Pool."""
        if self._target_temp == NO_CHANGE_REQUESTED:
            return
        if self._temp_check > 0:
            self._temp_check -= 1
            return

        current = float(status["set_temp"])
        if abs(current - self._target_temp) < 0.3:
            _LOGGER.warning(
                "Soll-Temperatur erreicht: %.1f °C attempts=%d",
                current, self._command_attempts,
            )
            self._target_temp = NO_CHANGE_REQUESTED
            self._temp_done.set()
            return

        if self._last_temp_seen is None:
            self._last_temp_seen = current

        prev = self._last_temp_seen
        delta = current - prev
        err_prev = abs(self._target_temp - prev)
        err_now = abs(self._target_temp - current)
        moved = abs(delta) >= 0.25
        code = getattr(self, "_last_temp_code", None)
        warmer_needed = self._target_temp > current
        if not hasattr(self, "_c6_score"):
            self._c6_score = {}

        if moved and err_now < err_prev - 0.15:
            # Fortschritt → Code belohnen, in richtiger Liste halten
            if code is not None:
                self._c6_score[code] = self._c6_score.get(code, 0) + 3
                if warmer_needed or delta > 0:
                    if code not in self._learned_c6_up:
                        self._learned_c6_up.append(code)
                    # aus DOWN entfernen wenn fälschlich dort
                    self._learned_c6_down = [c for c in self._learned_c6_down if c != code]
                if (not warmer_needed) or delta < 0:
                    if code not in self._learned_c6_down:
                        self._learned_c6_down.append(code)
                    self._learned_c6_up = [c for c in self._learned_c6_up if c != code]
            self._temp_steps_done = getattr(self, "_temp_steps_done", 0) + 1
            _LOGGER.warning(
                "Temp-Fortschritt: %.1f → %.1f (Ziel %.1f) code=%s score=%s | Pause 2s",
                prev, current, self._target_temp, code,
                self._c6_score.get(code, 0) if code else 0,
            )
            self._last_temp_seen = current
            self._temp_check = 4
            await asyncio.sleep(2.0)
            if self._target_temp == NO_CHANGE_REQUESTED:
                return
            await self._send_temp_step(self._target_temp > current)
            return

        if moved and err_now > err_prev + 0.15:
            # GEGEN → abstrafen und aus aktueller Richtungsliste
            if code is not None:
                self._c6_score[code] = self._c6_score.get(code, 0) - 5
                if warmer_needed:
                    self._learned_c6_up = [c for c in self._learned_c6_up if c != code]
                    if code not in self._learned_c6_down:
                        self._learned_c6_down.append(code)
                else:
                    self._learned_c6_down = [c for c in self._learned_c6_down if c != code]
                    if code not in self._learned_c6_up:
                        self._learned_c6_up.append(code)
            _LOGGER.warning(
                "Temp GEGEN: %.1f → %.1f code=%s → umsortiert score=%s",
                prev, current, code,
                self._c6_score.get(code, 0) if code else 0,
            )
            self._last_temp_seen = current
        else:
            # keine Bewegung
            if code is not None:
                self._c6_score[code] = self._c6_score.get(code, 0) - 1

        if self._command_attempts >= 100:
            _LOGGER.error(
                "set_temperature TIMEOUT | Ziel=%.1f got=%.1f attempts=%d",
                self._target_temp, current, self._command_attempts,
            )
            self._target_temp = NO_CHANGE_REQUESTED
            async with self._pending_lock:
                self._pending.clear()
            self._temp_done.set()
            return

        await self._send_temp_step(self._target_temp > current)
        self._temp_check = 3


    async def _try_direct_set(self, target: float) -> None:
        """MSG 0x20 Direct-Set – Fallback wenn Button-Codes stagnieren."""
        ch = self._assigned_channel or CMD_CHANNEL
        pkt = _build_set_temp(ch, target)
        _LOGGER.warning(
            "Direct-Set 0x20 Versuch | Ziel=%.1f pkt=%s",
            target, pkt.hex(" "),
        )
        await self._queue_raw(pkt)
        await self._wait_pending_clear(timeout=4.0)
        await asyncio.sleep(2.0)


    async def _send_light_step(self, color: bool = False) -> None:
        """Licht: CC 0xF1 = Ein/Aus, CC 0xF2 = Farbe (Log 13:26 bestätigt)."""
        if color:
            mtype, btn, b6 = CC_REQ, 0xF2, 0x00
        else:
            mtype, btn, b6 = CC_REQ, 0xF1, 0x00
        if self._light_attempt >= 12:
            _LOGGER.error("Licht-Versuche erschöpft – Abbruch")
            self._target_light_brightness = LIGHT_NO_CHANGE
            self._target_light_mode = LIGHT_NO_CHANGE
            async with self._pending_lock:
                self._pending.clear()
            self._light_done.set()
            return
        self._assigned_channel = CMD_CHANNEL
        await self._wait_pending_clear(timeout=2.0)
        _LOGGER.warning(
            "Licht-Schritt %s mtype=0x%02X btn=0x%02X attempt=%d ch=0x10",
            "COLOR" if color else "ON/OFF",
            mtype,
            btn,
            self._light_attempt + 1,
        )
        await self._queue_cc(btn, mtype, b6)
        self._light_attempt += 1

    async def _handle_light_feedback(self, lights: dict) -> None:
        if self._light_check > 0:
            self._light_check -= 1
        if self._light_check > 0:
            return

        if self._target_light_mode != LIGHT_NO_CHANGE:
            if lights["mode_raw"] == self._target_light_mode:
                _LOGGER.warning(
                    "Licht-Modus erreicht: %s (raw=%s)",
                    lights.get("mode"),
                    lights["mode_raw"],
                )
                self._target_light_mode = LIGHT_NO_CHANGE
                self._light_done.set()
            elif lights["brightness_raw"] == 0:
                await self._send_light_step(color=False)
                self._light_check = CHECKS_BEFORE_RETRY
            else:
                await self._send_light_step(color=True)
                self._light_check = CHECKS_BEFORE_RETRY
            return

        if self._target_light_brightness == LIGHT_NO_CHANGE:
            return

        if lights["brightness_raw"] == self._target_light_brightness:
            _LOGGER.warning(
                "Licht-Helligkeit erreicht: %s (Ziel=%s)",
                lights["brightness_raw"],
                self._target_light_brightness,
            )
            self._target_light_brightness = LIGHT_NO_CHANGE
            self._light_done.set()
            return

        # Ein/Aus: gleicher Button toggelt
        await self._send_light_step(color=False)
        self._light_check = CHECKS_BEFORE_RETRY

    async def send_button(self, btn: int, mtype: int = CC_REQ, b6: int = 0) -> None:
        await self._queue_cc(btn, mtype, b6)

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
        # Cameo: immer 0x10 – C6/CC auf anderen Kanälen greifen nicht
        self._assigned_channel = CMD_CHANNEL
        return CMD_CHANNEL

    async def _ensure_temp_range(self, target: float, current_raw: int) -> None:
        """Cameo 880: Temperaturbereich (Low/High) vor Feineinstellung umschalten."""
        high_range = current_raw >= 80
        want_high = target >= 37.0
        if high_range != want_high:
            if want_high:
                await self._queue_cc(BTN_TEMP_RANGE_HI)
            else:
                await self._queue_cc(BTN_TEMP_RANGE_LOW)
            _LOGGER.info(
                "Temperaturbereich umschalten: %s (raw_d8=%s, target=%.1f)",
                "HIGH" if want_high else "LOW",
                current_raw,
                target,
            )
            await self._wait_pending_clear()
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
                await self._wait_pending_clear()
            if snap["pump2"]:
                await self._queue_cc(BTN_PUMP2)
                await self._wait_pending_clear()
            await asyncio.sleep(0.8)

    async def set_temperature(self, target: float) -> None:
        """Solltemperatur per Klartext-CC wie HyperActiveJ (225/226)."""
        target = max(20.0, min(40.0, round(target * 2) / 2.0))
        async with self._cmd_lock:
            self._target_temp = NO_CHANGE_REQUESTED
            self._temp_done.set()
            async with self._pending_lock:
                self._pending.clear()
            await asyncio.sleep(0.2)
            self._temp_done.clear()

            snap = await self._status_snapshot()
            if not snap:
                raise UpdateFailed("Kein Status vom Spa")
            if abs(snap["set_temp"] - target) < 0.3:
                return

            ch = await self._ensure_channel()
            self._cc_queued = 0
            self._cc_sent = 0
            self._command_attempts = 0
            self._temp_check = 0
            self._temp_steps_done = 0
            self._temp_send_mode = 0
            self._last_temp_seen = float(snap["set_temp"])
            self._debug_cmd = True
            self._target_temp = target

            _LOGGER.warning(
                "set_temperature START | Ziel=%.1f aktuell=%.1f kanal=0x%02X "
                "| Modus=C6-Learn + ENC-CC + FIXED-C6",
                target, snap["set_temp"], ch,
            )

            # Menü verlassen falls nötig
            if snap.get("in_menu"):
                await self._queue_cc(BTN_MENU)
                await self._wait_pending_clear()

            # Ersten Schritt sofort anstoßen
            warmer = target > snap["set_temp"]
            await self._send_temp_step(warmer)
            self._temp_check = CHECKS_BEFORE_RETRY

            # Warten bis Feedback-Schleife fertig (Status-getrieben)
            # Max ~64 Versuche * ~1s Status ≈ 90s
            try:
                await asyncio.wait_for(self._temp_done.wait(), timeout=90.0)
            except asyncio.TimeoutError:
                final = await self._status_snapshot()
                got = final["set_temp"] if final else -1
                _LOGGER.error(
                    "set_temperature TIMEOUT | Ziel=%.1f got=%.1f sent=%d attempts=%d",
                    target, got, self._cc_sent, self._command_attempts,
                )
                self._target_temp = NO_CHANGE_REQUESTED
                async with self._pending_lock:
                    self._pending.clear()
                raise UpdateFailed(
                    f"Solltemperatur nicht erreicht (ist {got}, Ziel {target})"
                )
            finally:
                self._debug_cmd = False

            final = await self._status_snapshot()
            _LOGGER.warning(
                "set_temperature OK | Ziel=%.1f final=%.1f attempts=%d sent=%d",
                target,
                final["set_temp"] if final else -1,
                self._command_attempts,
                self._cc_sent,
            )


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
            self._debug_cmd = True
            self._cc_queued = 0
            self._cc_sent = 0
            self._light_attempt = 0
            self._light_done.clear()
            self._light_check = 0
            self._target_light_mode = LIGHT_NO_CHANGE
            self._target_light_brightness = LIGHT_NO_CHANGE
            lights0 = await self._lights_snapshot()
            _LOGGER.info(
                "set_light START | on=%s brightness_pct=%s effect=%s | "
                "aktuell on=%s bright=%s mode=%s | kanal=0x%02X",
                on,
                brightness_pct,
                effect,
                lights0.get("on") if lights0 else None,
                lights0.get("brightness_raw") if lights0 else None,
                lights0.get("mode") if lights0 else None,
                self._assigned_channel or 0,
            )

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
                _LOGGER.info(
                    "set_light OK | sent=%d queued=%d last_cc=%s",
                    self._cc_sent,
                    self._cc_queued,
                    self._last_cc_hex,
                )
            except asyncio.TimeoutError as exc:
                lights = await self._lights_snapshot()
                state = "an" if lights and lights.get("on") else "aus"
                _LOGGER.error(
                    "set_light TIMEOUT | aktuell=%s bright=%s mode=%s | "
                    "sent=%d queued=%d last_cc=%s kanal=0x%02X | "
                    "Bitte am Panel Licht drücken und nach 'PANEL-TASTE erkannt' suchen",
                    state,
                    lights.get("brightness_raw") if lights else None,
                    lights.get("mode") if lights else None,
                    self._cc_sent,
                    self._cc_queued,
                    self._last_cc_hex,
                    self._assigned_channel or 0,
                )
                raise UpdateFailed(
                    f"Licht-Zielzustand nicht erreicht (aktuell {state}, "
                    f"gesendet={self._cc_sent}/{self._cc_queued})"
                ) from exc
            finally:
                self._target_light_brightness = LIGHT_NO_CHANGE
                self._target_light_mode = LIGHT_NO_CHANGE
                self._debug_cmd = False


# ── DataUpdateCoordinator ────────────────────────────────────────────────────

class SpaCoordinator(DataUpdateCoordinator):
    """Koordiniert Daten-Updates und heilt die Verbindung selbst."""

    def __init__(self, hass: HomeAssistant, client: SpaClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=5),
        )
        self.client = client
        self._heal_failures = 0

    async def _async_update_data(self) -> dict:
        # Selbstheilung: reconnect wenn tot oder keine Status-Pakete
        need_reconnect = False
        if not self.client.is_connected:
            need_reconnect = True
            reason = "nicht verbunden"
        elif self.client._last_status_ts and (
            time.monotonic() - self.client._last_status_ts > 25.0
        ):
            need_reconnect = True
            reason = "kein Status seit >25s"
        elif self.client.status is None:
            need_reconnect = True
            reason = "noch kein Status"

        if need_reconnect:
            self._heal_failures += 1
            _LOGGER.warning(
                "Selbstheilung (%d): %s – reconnect…",
                self._heal_failures,
                reason,
            )
            try:
                await self.client.disconnect()
            except Exception:  # noqa: BLE001
                pass
            await asyncio.sleep(min(2.0 * self._heal_failures, 15.0))
            try:
                await self.client.connect()
                await self.client.wait_ready(timeout=12.0)
                self._heal_failures = 0
                _LOGGER.warning(
                    "Selbstheilung OK – Kanal 0x%02X",
                    self.client.assigned_channel or 0,
                )
            except Exception as exc:
                raise UpdateFailed(f"Reconnect fehlgeschlagen: {exc}") from exc

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
