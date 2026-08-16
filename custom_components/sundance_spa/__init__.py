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
C6_REQ            = 0xC6  # Cameo iTouch Temp/UI (Panel-Sniff 2026-08-15)
C7_REQ            = 0xC7  # Cameo Licht-Befehl (Panel-Sniff 2026-08-16)
C2_UPDATE       = 0xC2  # Cameo Licht-Stream während Blend
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
PENDING_WAIT_S        = 1.5 # max. Warten bis CTS; danach Force-Send

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
# Cameo 880: Panel sendet C7 (verschlüsselt), Klartext nach Decrypt = 0x2F / 0x33
# Helligkeitsstufen am Panel: 0 / 20 / 40 / 60 / 80 / 100 (nicht 33/66)
LIGHT_C7_BTN       = 0x2F
LIGHT_C7_B6        = 0x33
LIGHT_ON_VARIANTS: tuple[tuple[int, int, int], ...] = (
    (C7_REQ, LIGHT_C7_BTN, LIGHT_C7_B6),
    (CC_REQ, 241, 0),
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
    elif packet_type == 0xC7:
        key1 = packet[5] ^ 0xC7  # Cameo Licht (Panel-Sniff 2026-08-16)
    elif packet_type == 0xC2:
        key1 = packet[5] ^ 0xC2  # Cameo Licht-Stream
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


def _build_c7(
    btn: int = LIGHT_C7_BTN,
    b6: int = LIGHT_C7_B6,
    channel: int = CMD_CHANNEL,
    key_byte: int = 0,
) -> bytes:
    """Cameo Licht-Befehl 0xC7 (verschlüsselt, Panel-Sniff: Klartext 0x2F/0x33)."""
    ml = 8
    msg = bytearray(10)
    msg[0] = M_STARTEND
    msg[1] = ml
    msg[2] = channel & 0xFF
    msg[3] = 0xBF
    msg[4] = C7_REQ
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

# Log-verifizierte C6-Codes (Cameo 880). GEGEN-Codes gehören NICHT in die
# jeweilige Richtungsliste – sie landen in der Blacklist und bleiben draußen.
# Format: (mtype, btn, b6)
TEMP_UP_CODES: list[tuple[int, int, int]] = [
    (0xC6, 0xF0, 0x47),  # konsistent UP
    (0xC6, 0x52, 0xE7),
]
TEMP_DOWN_CODES: list[tuple[int, int, int]] = [
    (0xC6, 0x74, 0xC5),  # Log: zuverlässig DOWN
    (0xC6, 0x8E, 0x3C),  # Log: zuverlässig DOWN
    (0xC6, 0x15, 0xA5),  # Log: DOWN
    (0xC6, 0xC8, 0x7C),  # gemischt, aber oft DOWN
]
# Bekannt problematisch bei DOWN (verursachen GEGEN/UP) – Start-Blacklist
TEMP_DOWN_BAD: list[tuple[int, int, int]] = [
    (0xC6, 0xEC, 0x59),
    (0xC6, 0x98, 0x2B),
]
MSG_SET_TEMP = 0x20
TEMP_UP_C6 = tuple((b, x) for m, b, x in TEMP_UP_CODES if m == 0xC6)
TEMP_DOWN_C6 = tuple((b, x) for m, b, x in TEMP_DOWN_CODES if m == 0xC6)
# Klartext-CC Fallback (HyperActiveJ / Sundance 780)
TEMP_UP_VARIANTS: tuple[tuple[int, int], ...] = (
    (BTN_TEMP_UP, 0),
)
TEMP_DOWN_VARIANTS: tuple[tuple[int, int], ...] = (
    (BTN_TEMP_DOWN, 0),
)
TEMP_RANGE_HI_CC_BTN = 141
TEMP_RANGE_HI_CC_B6  = 69
BTN_MENU = 254

# Seed aus Panel-Sniff 11:41 (frisch, Log-verifiziert)
SEED_DOWN_C6: list[tuple[int, int, int]] = [
    (0xC6, 0x92, 0x22),  # 28.5→28.0
    (0xC6, 0x4E, 0xFF),  # 29.0→28.5
    (0xC6, 0xF8, 0x4A),  # 29.5→29.0
    (0xC6, 0x0E, 0xBD),  # 30.0→29.5
    (0xC6, 0x42, 0xF6),  # 30.5→30.0
    (0xC6, 0x48, 0xFD),  # 31.0→30.5
    (0xC6, 0x86, 0x30),  # 31.5→31.0
    (0xC6, 0xC3, 0x74),  # 32.0→31.5
]
SEED_UP_C6: list[tuple[int, int, int]] = [
    (0xC6, 0x68, 0xDF),  # 31.0→31.5
    (0xC6, 0xA7, 0x6F),  # 31.5→32.0
    (0xC6, 0x58, 0x91),  # 32.0→32.5
    (0xC6, 0xA6, 0x6C),  # 32.5→33.0
    (0xC6, 0x07, 0xCC),  # 33.0→33.5
    (0xC6, 0xF0, 0x3C),  # 33.5→34.0
    (0xC6, 0x14, 0xD3),  # 39.0→39.5
    (0xC6, 0x67, 0xA1),  # 38.5→39.0
]

# Cameo: C6-Codes sitzungsabhängig; Lock bei Erfolg; Rate-Limit gegen Burst.
# Direct-Set 0x20 oft ignoriert → C6 primär, CC Fallback, 0x20 selten.
TEMP_STALL_BEFORE_FALLBACK = 5
TEMP_MAX_ATTEMPTS = 28
TEMP_MAX_ATTEMPTS_HIGH = 42       # High-Range: mehr Exploration statt denselben Code zu hämmern
TEMP_LEARN_POOL_MAX = 16
TEMP_BUCKET_POOL_MAX = 12
TEMP_RANGE_JUMP_C = 2.5
TEMP_LEARN_STRONG_DELTA = 0.5
TEMP_LEARN_WEAK_DELTA = 1.0
TEMP_EXPLORATION_AFTER = 3         # nach N erfolglosen C6-Versuchen gezielt neue Codes testen
TEMP_STRONG_LOCK_SCORE = 12
TEMP_FLOOR_PROBE = 4
TEMP_STATUS_WAIT = 5              # Status-Frames warten nach TX (~1–1.5s)
TEMP_MIN_STEP_GAP = 0.9           # min. Sekunden zwischen Temp-Befehlen
TEMP_SPA_FLOOR_C = 28.0           # typisches Cameo-Minimum (High-Range)
TEMP_CLEAN_STEP_MAX = 0.6         # nur ±0.5-Schritte dürfen STRONG-LOCK auslösen
TEMP_JUMP_UNLOCK = 1.2            # bei Δ≥1.2°C Lock sofort lösen (kein echter Schritt)
TEMP_OSCILLATION_LIMIT = 3        # Range-Sprünge hintereinander → aggressiv entsperren

# Temperaturabhängige C6-Lernbereiche. Der Pool ist absichtlich nicht global:
# Codes aus dem 28-32°C-Bereich sollen bei 36-39°C nicht mehr bevorzugt werden.
# WICHTIG: Ein Temp-Schritt passiert IMMER am aktuellen Wert (±0.5), nie am Ziel.
# Deshalb primär current_bucket wählen, nicht target_bucket.
TEMP_BUCKETS: tuple[tuple[float, float], ...] = (
    (26.0, 29.0),
    (29.0, 33.0),
    (33.0, 37.0),
    (37.0, 40.5),
)

# Synthetische Exploration-Kandidaten (Cameo C6 btn/b6), wenn Bucket leer ist.
# Abgeleitet aus Log-Mustern (0x92/22, 0xF8/4A, 0x0E/BD, 0x68/DF, …) + Variationen.
TEMP_EXPLORE_C6: tuple[tuple[int, int, int], ...] = (
    (0xC6, 0x92, 0x22), (0xC6, 0xF8, 0x4A), (0xC6, 0x0E, 0xBD),
    (0xC6, 0x42, 0xF6), (0xC6, 0x48, 0xFD), (0xC6, 0x4E, 0xFF),
    (0xC6, 0x86, 0x30), (0xC6, 0xC3, 0x74), (0xC6, 0x68, 0xDF),
    (0xC6, 0xA7, 0x6F), (0xC6, 0x58, 0x91), (0xC6, 0xA6, 0x6C),
    (0xC6, 0x07, 0xCC), (0xC6, 0xF0, 0x3C), (0xC6, 0x14, 0xD3),
    (0xC6, 0x67, 0xA1), (0xC6, 0xF0, 0x47), (0xC6, 0x52, 0xE7),
    (0xC6, 0x74, 0xC5), (0xC6, 0x8E, 0x3C), (0xC6, 0x15, 0xA5),
    (0xC6, 0xC8, 0x7C), (0xC6, 0x1A, 0xB0), (0xC6, 0x3D, 0xC1),
    (0xC6, 0x5B, 0x88), (0xC6, 0x9C, 0x55), (0xC6, 0xB2, 0x19),
    (0xC6, 0xD4, 0x2E), (0xC6, 0xE1, 0x0A), (0xC6, 0x2F, 0xD8),
)



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
    """Cameo-Helligkeitsstufen (0 / 20 / 40 / 60 / 80 / 100)."""
    if level_pct <= 0:
        return 0
    if level_pct <= 20:
        return 20
    if level_pct <= 40:
        return 40
    if level_pct <= 60:
        return 60
    if level_pct <= 80:
        return 80
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
        # Globale Blacklists (nur für hart kaputte Codes) + per-Bucket-Blacklist
        self._temp_blacklist_up: set = set()
        self._temp_blacklist_down: set = set()
        # key: (direction_is_up: 0|1, bucket_idx, btn, b6) → True
        self._c6_bucket_blacklist: set[tuple[int, int, int, int]] = set()
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
        # Bucket-basierte Pools: direction -> bucket -> [C6 entries]
        self._c6_buckets: dict[str, dict[int, list]] = {
            "up": {i: [] for i in range(len(TEMP_BUCKETS))},
            "down": {i: [] for i in range(len(TEMP_BUCKETS))},
        }
        self._c6_bucket_score: dict[tuple[int, int, int, int], int] = {}
        self._c6_bucket_last_seen: dict[tuple[int, int, int, int], float] = {}
        self._temp_exploration_round = 0
        self._temp_target_bucket: int | None = None
        self._temp_current_bucket: int | None = None
        self._panel_c6_recent: list = []  # (ts, btn, b6, observed_temp)
        self._last_set_temp_seen: float | None = None
        self._temp_locked_code = None
        self._c6_score: dict = {}
        self._temp_send_mode = 0  # 0=C6, 1=Klartext-CC, 2=Direct-Set 0x20
        self._temp_stall_rounds = 0
        self._temp_progress_at = 0
        self._temp_jump_streak = 0  # aufeinanderfolgende Range/Unit-Sprünge
        self._temp_stable_anchor: float | None = None  # letzter „sauberer“ Sollwert
        self._temp_range_forced = False  # High-Range in diesem set_temperature schon gesendet
        self._sniff_panel_cc = True
        self._last_logged_display: int | None = None
        self._last_logged_set: float | None = None
        self._target_light_brightness = LIGHT_NO_CHANGE
        self._target_light_mode = LIGHT_NO_CHANGE
        self._light_done = asyncio.Event()
        self._light_check = 0
        self._recent_bus: list[tuple[float, str]] = []  # (ts, summary)
        self._last_light_bright: int | None = None
        # Problematische DOWN-Codes von Anfang an blacklisten (global)
        for bad in TEMP_DOWN_BAD:
            self._temp_blacklist_down.add(bad)
        self._seed_c6_buckets()

    def _temp_bucket(self, temp: float) -> int:
        """Liefert den Temperatur-Bucket; Grenzwerte sind halboffen."""
        for idx, (low, high) in enumerate(TEMP_BUCKETS):
            if low <= temp < high:
                return idx
        if temp < TEMP_BUCKETS[0][0]:
            return 0
        return len(TEMP_BUCKETS) - 1

    def _bucket_label(self, bucket: int) -> str:
        low, high = TEMP_BUCKETS[max(0, min(bucket, len(TEMP_BUCKETS) - 1))]
        return f"{low:.0f}-{high:.1f}"

    def _seed_c6_buckets(self) -> None:
        """Verteilt bekannte Seeds in passende Temperaturbereiche.

        DOWN-Seeds stammen aus Logs um 28–32 °C. UP-Seeds decken 31–34 und
        38–39 ab. Für 33–37 fehlen oft Codes → Exploration füllt nach.
        """
        seed_ranges_up = (31.0, 31.5, 32.0, 32.5, 33.0, 33.5, 39.0, 38.5)
        seed_ranges_down = (28.5, 29.0, 29.5, 30.0, 30.5, 31.0, 31.5, 32.0)
        for entry, temp in zip(SEED_UP_C6, seed_ranges_up):
            b = self._temp_bucket(temp)
            if entry not in self._c6_buckets["up"][b]:
                self._c6_buckets["up"][b].append(entry)
                key = (1, b, entry[1], entry[2])
                self._c6_bucket_score[key] = self._c6_bucket_score.get(key, 0) + 2
        for entry, temp in zip(SEED_DOWN_C6, seed_ranges_down):
            b = self._temp_bucket(temp)
            if entry not in self._c6_buckets["down"][b]:
                self._c6_buckets["down"][b].append(entry)
                key = (0, b, entry[1], entry[2])
                self._c6_bucket_score[key] = self._c6_bucket_score.get(key, 0) + 2
        # UP-Seeds zusätzlich in Nachbar-Buckets legen, damit 33–37 nicht leer startet
        for entry in SEED_UP_C6[:6]:
            for b in (1, 2):  # 29–33 und 33–37
                if entry not in self._c6_buckets["up"][b]:
                    self._c6_buckets["up"][b].append(entry)

    def _add_c6_to_bucket(
        self, warmer: bool, entry: tuple[int, int, int], temp: float, score_delta: int = 0
    ) -> int:
        direction = "up" if warmer else "down"
        bucket = self._temp_bucket(temp)
        pool = self._c6_buckets[direction][bucket]
        if entry in pool:
            pool.remove(entry)
        pool.append(entry)
        del_count = max(0, len(pool) - TEMP_BUCKET_POOL_MAX)
        if del_count:
            del pool[:del_count]
        key = (1 if warmer else 0, bucket, entry[1], entry[2])
        self._c6_bucket_score[key] = self._c6_bucket_score.get(key, 0) + score_delta
        self._c6_bucket_last_seen[key] = time.monotonic()
        # Erfolg in diesem Bucket → Blacklist-Eintrag dort löschen
        self._c6_bucket_blacklist.discard(key)
        return bucket

    def _remove_c6_from_bucket(
        self, warmer: bool, entry: tuple[int, int, int], temp: float
    ) -> None:
        """Entfernt Code nur aus dem Bucket, in dem er versagt hat (nicht global)."""
        direction = "up" if warmer else "down"
        bucket = self._temp_bucket(temp)
        pool = self._c6_buckets[direction][bucket]
        if entry in pool:
            pool.remove(entry)
        key = (1 if warmer else 0, bucket, entry[1], entry[2])
        self._c6_bucket_blacklist.add(key)
        self._c6_bucket_score[key] = self._c6_bucket_score.get(key, 0) - 4

    def _remove_c6_from_buckets(self, entry: tuple[int, int, int]) -> None:
        """Legacy: aus allen Buckets entfernen (nur bei hartem Fail / Panel-Relearn)."""
        for direction in ("up", "down"):
            for pool in self._c6_buckets[direction].values():
                if entry in pool:
                    pool.remove(entry)

    def _is_bucket_blacklisted(
        self, warmer: bool, entry: tuple[int, int, int], bucket: int
    ) -> bool:
        if entry in self._temp_blacklist(warmer):
            return True
        key = (1 if warmer else 0, bucket, entry[1], entry[2])
        return key in self._c6_bucket_blacklist

    def _adaptive_temp_attempts(self, target: float | None = None) -> int:
        target = self._target_temp if target is None else target
        if target is not None and target >= 33.0:
            return TEMP_MAX_ATTEMPTS_HIGH
        return TEMP_MAX_ATTEMPTS

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

                # Immer flushen wenn Pending wartet – Cameo oft nur 1 Kanal
                if self._pending:
                    if (
                        self._assigned_channel is not None
                        and channel == self._assigned_channel
                    ):
                        self._cts_own += 1
                        await self._flush_pending(channel)
                    elif self._assigned_channel is not None:
                        self._cts_own += 1
                        await self._flush_pending(self._assigned_channel)
                    else:
                        await self._flush_pending(channel)

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
                        self._note_bus(
                            f"CC {src} ch=0x{channel:02X} "
                            f"btn=0x{btn_b5:02X} b6=0x{b6:02X} {raw_hex}"
                        )
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
                raw_hex = msg.hex(" ")
                recent = getattr(self, "_recent_tx", set())
                is_ours = raw_hex in recent

                if mtype == C6_REQ and len(msg) >= 7 and not is_ours:
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
                        self._note_bus(
                            f"C6-ENC btn=0x{btn:02X} b6=0x{b6:02X} {raw_hex}"
                        )
                    else:
                        btn, b6 = msg[5], msg[6] if len(msg) > 6 else 0
                        _LOGGER.warning(
                            "PANEL-SNIFF C6 | ch=0x%02X btn=0x%02X b6=0x%02X "
                            "xor=0x%02X sum=0x%02X raw=%s",
                            channel, btn, b6, btn ^ b6, (btn + b6) & 0xFF, raw_hex,
                        )
                        self._note_bus(
                            f"C6 ch=0x{channel:02X} btn=0x{btn:02X} "
                            f"b6=0x{b6:02X} {raw_hex}"
                        )
                    self._panel_c6_recent.append((time.monotonic(), btn, b6, float(self._status.get("set_temp")) if self._status and self._status.get("set_temp") is not None else None))
                    if len(self._panel_c6_recent) > 80:
                        self._panel_c6_recent = self._panel_c6_recent[-60:]
                elif mtype == C7_REQ and len(msg) >= 9:
                    # Cameo Licht-Befehl (verschlüsselt)
                    if not is_ours:
                        dec = _jacuzzi_xor_cipher(bytearray(msg), encrypt=False)
                        btn = dec[6] if len(dec) > 6 else 0
                        b6 = dec[7] if len(dec) > 7 else 0
                        _LOGGER.warning(
                            "PANEL-SNIFF C7 | ch=0x%02X key=0x%02X "
                            "btn=0x%02X b6=0x%02X raw=%s",
                            channel, msg[5], btn, b6, raw_hex,
                        )
                        self._note_bus(
                            f"C7 PANEL btn=0x{btn:02X} b6=0x{b6:02X} {raw_hex}"
                        )
                    else:
                        self._note_bus(f"C7 EIGEN {raw_hex}")
                elif mtype == C2_UPDATE:
                    # Licht-Stream während Blend – nur Ringpuffer
                    self._note_bus(f"C2 {raw_hex}")
                else:
                    # Unbekannte Typen immer im Ringpuffer (auch ohne debug_cmd)
                    self._note_bus(
                        f"UNK ch=0x{channel:02X} mtype=0x{mtype:02X} "
                        f"{msg.hex(' ')}"
                    )
                    if self._debug_cmd:
                        _LOGGER.warning(
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
                    bright = int(dec.get("brightness_raw") or 0)
                    mode_raw = dec.get("mode_raw")
                    prev_b = self._last_light_bright
                    if prev_b is None or prev_b != bright:
                        buf = list(self._recent_bus[-12:])
                        _LOGGER.warning(
                            "LIGHT-CHANGE | bright %s→%s mode=%s mode_raw=%s "
                            "on=%s | recent_bus=%s",
                            prev_b,
                            bright,
                            dec.get("mode"),
                            mode_raw,
                            dec.get("on"),
                            buf,
                        )
                        self._last_light_bright = bright
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

    def _note_bus(self, summary: str) -> None:
        """Ringpuffer der letzten Bus-Nachrichten (für Licht-Korrelation)."""
        self._recent_bus.append((round(time.monotonic(), 2), summary))
        if len(self._recent_bus) > 40:
            self._recent_bus = self._recent_bus[-30:]

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
        """Sendet ein wartendes Paket (Kanal-Match oder erstes bei Solo/Cameo)."""
        assert self._writer is not None
        async with self._pending_lock:
            if not self._pending:
                return
            idx_send = None
            for idx, (pkt_ch, pkt) in enumerate(self._pending):
                if pkt_ch == channel:
                    idx_send = idx
                    break
            if idx_send is None:
                # Cameo: trotzdem senden – sonst bleibt Pending ewig stecken
                idx_send = 0
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

    async def _force_send_pending(self) -> None:
        """Notfall: steckengebliebenes Pending sofort senden (ohne CTS)."""
        assert self._writer is not None
        async with self._pending_lock:
            if not self._pending:
                return
            pkt_ch, pkt = self._pending.pop(0)
        self._writer.write(pkt)
        await self._writer.drain()
        self._cc_sent += 1
        self._last_cc_hex = pkt.hex(" ")
        _LOGGER.warning(
            "TX FORCE (kein CTS) pkt_ch=0x%02X sent=%d: %s",
            pkt_ch, self._cc_sent, self._last_cc_hex,
        )

    async def _wait_pending_clear(self, timeout: float = PENDING_WAIT_S) -> bool:
        """Wartet auf leere Pending-Queue; bei Timeout Force-Send."""
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
                "Pending-CC nach %.1fs stecken (%d) cts_own=%d → Force-Send",
                timeout, left, self._cts_own,
            )
            await self._force_send_pending()
            return True
        return True

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
        """Reiht ein fertiges Paket ein (C7/C6/Direct-Set).

        Nicht blockieren (kann aus Receiver kommen). Bei vollem Slot:
        ältestes verwerfen und neues einreihen.
        """
        ch = await self._ensure_channel()
        # Kanal im Paket ggf. anpassen (Byte 2) + CS neu
        if len(pkt) > 2:
            ba = bytearray(pkt)
            ba[2] = ch & 0xFF
            ml = ba[1]
            if len(ba) > ml:
                ba[ml] = _calc_cs(ba[1:ml], ml - 1)
            pkt = bytes(ba)

        async with self._pending_lock:
            if len(self._pending) >= MAX_PENDING_CC:
                dropped = self._pending.pop(0)
                _LOGGER.warning(
                    "Pending-RAW verworfen (Slot voll): %s",
                    dropped[1].hex(" "),
                )
            self._pending.append((ch, pkt))
            pending_n = len(self._pending)

        self._cc_queued += 1
        _LOGGER.warning(
            "RAW eingeplant ch=0x%02X pending=%d queued=%d: %s",
            ch, pending_n, self._cc_queued, pkt.hex(" "),
        )
    async def send_blower_toggle(self) -> None:
        """Blubber ein/aus – verschlüsseltes Panel-CC (53/217)."""
        await self._queue_cc(BLOWER_CC_BTN, CC_REQ, BLOWER_CC_B6)


    def _learn_c6_from_settemp(self, new_set: float) -> None:
        """Ordnet echte Panel-C6-Temperaturschritte einem Temperatur-Bucket zu.

        0.5°C wird stark gelernt, 1.0°C nur schwach. Große Sprünge werden als
        Range/Unit-Umschaltung behandelt. Ein während einer laufenden Automation
        empfangener C6 wird nicht als Panel-Learning gewertet.
        """
        prev = self._last_set_temp_seen
        self._last_set_temp_seen = new_set
        if prev is None:
            return
        delta = new_set - prev
        abs_delta = abs(delta)
        if abs_delta < 0.25 or abs_delta >= TEMP_RANGE_JUMP_C:
            if abs_delta >= TEMP_RANGE_JUMP_C:
                _LOGGER.warning(
                    "LEARN skip (Range/Unit?): %.1f→%.1f Δ=%.1f", prev, new_set, delta
                )
            return
        if self._target_temp != NO_CHANGE_REQUESTED:
            return

        now = time.monotonic()
        recent = [
            item for item in self._panel_c6_recent
            if now - item[0] < 1.2
        ]
        if not recent:
            return
        ts, btn, b6, observed_temp = recent[-1]
        entry = (0xC6, btn, b6)

        # Derive the bucket from the actual step, not merely the current target.
        step_temp = min(prev, new_set) if delta < 0 else prev
        bucket = self._temp_bucket(step_temp)
        strong = abs_delta <= TEMP_LEARN_STRONG_DELTA + 0.01
        weak = abs_delta <= TEMP_LEARN_WEAK_DELTA + 0.01
        if not weak:
            return
        score_delta = 5 if strong else 1
        direction = delta > 0

        self._remove_c6_from_buckets(entry)
        self._add_c6_to_bucket(direction, entry, step_temp, score_delta)
        if direction:
            if entry in self._learned_c6_down:
                self._learned_c6_down.remove(entry)
            if entry in self._learned_c6_up:
                self._learned_c6_up.remove(entry)
            self._learned_c6_up.append(entry)
            self._learned_c6_up = self._learned_c6_up[-TEMP_LEARN_POOL_MAX:]
            self._temp_blacklist_up.discard(entry)
        else:
            if entry in self._learned_c6_up:
                self._learned_c6_up.remove(entry)
            if entry in self._learned_c6_down:
                self._learned_c6_down.remove(entry)
            self._learned_c6_down.append(entry)
            self._learned_c6_down = self._learned_c6_down[-TEMP_LEARN_POOL_MAX:]
            self._temp_blacklist_down.discard(entry)

        self._c6_score[entry] = max(self._c6_score.get(entry, 0), 0) + score_delta
        self._c6_bucket_last_seen[(1 if direction else 0, bucket, btn, b6)] = now
        if self._c6_score[entry] >= TEMP_STRONG_LOCK_SCORE:
            self._temp_locked_code = entry

        _LOGGER.warning(
            "LEARN-%s C6 btn=0x%02X b6=0x%02X %.1f→%.1f Δ=%.1f "
            "bucket=%s strength=%s score=%d",
            "UP" if direction else "DOWN", btn, b6, prev, new_set, delta,
            self._bucket_label(bucket), "strong" if strong else "weak",
            self._c6_score[entry],
        )

    def _temp_blacklist(self, warmer: bool) -> set:
        return self._temp_blacklist_up if warmer else self._temp_blacklist_down

    def _pick_temp_codes(self, warmer: bool) -> list[tuple[int, int, int]]:
        """C6-Auswahl am aktuellen Temperaturpunkt (nicht am Ziel).

        Jeder Schritt ist ±0.5 am *aktuellen* Soll. Deshalb:
        1. current_bucket zuerst
        2. Nachbar-Buckets in Schritt-Richtung
        3. Lock nur wenn er im current±1-Bucket vorkommt
        4. Bei leerem/stagnierendem Pool: Exploration (TEMP_EXPLORE_C6)
        """
        direction = "up" if warmer else "down"
        bl = self._temp_blacklist(warmer)
        scores = getattr(self, "_c6_score", {})
        target = float(self._target_temp) if self._target_temp != NO_CHANGE_REQUESTED else 30.0
        current = float(
            self._last_temp_seen if self._last_temp_seen is not None else target
        )
        target_bucket = self._temp_bucket(target)
        current_bucket = self._temp_bucket(current)
        self._temp_target_bucket = target_bucket
        self._temp_current_bucket = current_bucket

        # Schritt passiert am aktuellen Wert → current zuerst, dann Richtung Ziel
        bucket_order: list[int] = [current_bucket]
        step_dir = 1 if warmer else -1
        neighbor = current_bucket + step_dir
        if 0 <= neighbor < len(TEMP_BUCKETS) and neighbor not in bucket_order:
            bucket_order.append(neighbor)
        # leichten Fallback auf target_bucket erlauben, wenn weit entfernt
        if target_bucket not in bucket_order:
            bucket_order.append(target_bucket)
        other = current_bucket - step_dir
        if 0 <= other < len(TEMP_BUCKETS) and other not in bucket_order:
            bucket_order.append(other)

        candidates: list[tuple[int, int, int]] = []
        locked = getattr(self, "_temp_locked_code", None)
        if (
            locked
            and isinstance(locked, tuple)
            and len(locked) == 3
            and locked[0] == 0xC6
            and locked not in bl
            and not self._is_bucket_blacklisted(warmer, locked, current_bucket)
        ):
            lock_ok = any(
                locked in self._c6_buckets[direction][b]
                for b in bucket_order[:2]
            )
            if lock_ok or scores.get(locked, 0) >= TEMP_STRONG_LOCK_SCORE:
                candidates.append(locked)

        for bucket in bucket_order:
            pool = list(self._c6_buckets[direction][bucket])
            pool.sort(
                key=lambda c: (
                    self._c6_bucket_score.get(
                        (1 if warmer else 0, bucket, c[1], c[2]), 0
                    )
                    + scores.get(c, 0),
                    self._c6_bucket_last_seen.get(
                        (1 if warmer else 0, bucket, c[1], c[2]), 0.0
                    ),
                ),
                reverse=True,
            )
            for code in pool:
                if code in candidates:
                    continue
                if self._is_bucket_blacklisted(warmer, code, bucket):
                    continue
                if scores.get(code, 0) < -5:
                    continue
                candidates.append(code)
                if len(candidates) >= 8:
                    return candidates

        # Exploration: wenn wenig Kandidaten oder Stall → frische Codes
        need_explore = (
            len(candidates) < 3
            or self._temp_stall_rounds >= TEMP_EXPLORATION_AFTER
        )
        if need_explore:
            self._temp_exploration_round += 1
            explore_idx = self._temp_exploration_round
            # Zyklisch durch TEMP_EXPLORE_C6, unbekannte zuerst
            ordered = list(TEMP_EXPLORE_C6)
            start = explore_idx % max(1, len(ordered))
            ordered = ordered[start:] + ordered[:start]
            for code in ordered:
                if code in candidates or code in bl:
                    continue
                if self._is_bucket_blacklisted(warmer, code, current_bucket):
                    continue
                candidates.append(code)
                # in current_bucket eintragen, damit Erfolg dort landet
                pool = self._c6_buckets[direction][current_bucket]
                if code not in pool:
                    pool.append(code)
                if len(candidates) >= 8:
                    break
            _LOGGER.warning(
                "Temp-Exploration %s cur=%.1f bucket=%s candidates=%d stall=%d",
                "UP" if warmer else "DOWN",
                current,
                self._bucket_label(current_bucket),
                len(candidates),
                self._temp_stall_rounds,
            )

        return candidates

    async def _send_temp_step(self, warmer: bool) -> None:
        """C6 primär → CC → selten Direct. Rate-Limit + Pending-Flush."""
        if self._assigned_channel is None:
            self._assigned_channel = CMD_CHANNEL

        max_attempts = self._adaptive_temp_attempts()
        if self._command_attempts >= max_attempts:
            _LOGGER.error("Temp: >=%d Versuche – Abbruch", max_attempts)
            self._target_temp = NO_CHANGE_REQUESTED
            async with self._pending_lock:
                self._pending.clear()
            self._temp_done.set()
            return

        # Rate-Limit: nicht schneller als TEMP_MIN_STEP_GAP senden
        last_ts = getattr(self, "_last_temp_cmd_ts", 0.0) or 0.0
        gap = time.monotonic() - last_ts
        if gap < TEMP_MIN_STEP_GAP:
            await asyncio.sleep(TEMP_MIN_STEP_GAP - gap)

        await self._wait_pending_clear(timeout=PENDING_WAIT_S)

        stall = getattr(self, "_temp_stall_rounds", 0)
        # CC ab stall≥5, Direct nur sehr spät und selten
        use_cc = stall >= TEMP_STALL_BEFORE_FALLBACK and (stall % 3 != 0)
        use_direct = stall >= TEMP_STALL_BEFORE_FALLBACK * 3 and (stall % 5 == 0)

        if use_direct:
            await self._try_direct_set(self._target_temp)
            self._last_temp_code = ("direct", 0x20, 0)
            self._last_temp_warmer = warmer
            self._last_temp_cmd_ts = time.monotonic()
            self._command_attempts += 1
            self._temp_check = TEMP_STATUS_WAIT
            return

        if use_cc:
            btn = BTN_TEMP_UP if warmer else BTN_TEMP_DOWN
            _LOGGER.warning(
                "Temp-Schritt CC btn=%d attempt=%d stall=%d",
                btn, self._command_attempts + 1, stall,
            )
            self._last_temp_code = (0xCC, btn, 0)
            self._last_temp_warmer = warmer
            await self._queue_cc(btn, CC_REQ, 0)
            self._last_temp_cmd_ts = time.monotonic()
            self._command_attempts += 1
            self._temp_check = TEMP_STATUS_WAIT
            return

        codes = self._pick_temp_codes(warmer)
        if not codes:
            # Notfall: globale Blacklist lockern + Seeds zurück, Exploration erzwingen
            bl = self._temp_blacklist(warmer)
            bl.clear()
            self._temp_stall_rounds = max(
                self._temp_stall_rounds, TEMP_EXPLORATION_AFTER
            )
            seeds = SEED_UP_C6 if warmer else SEED_DOWN_C6
            pool = self._learned_c6_up if warmer else self._learned_c6_down
            for s in seeds:
                if s not in pool:
                    pool.append(s)
            cur = float(
                self._last_temp_seen
                if self._last_temp_seen is not None
                else (self._target_temp or 30.0)
            )
            b = self._temp_bucket(cur)
            direction = "up" if warmer else "down"
            for s in seeds:
                if s not in self._c6_buckets[direction][b]:
                    self._c6_buckets[direction][b].append(s)
            codes = self._pick_temp_codes(warmer)
        if not codes:
            btn = BTN_TEMP_UP if warmer else BTN_TEMP_DOWN
            self._last_temp_code = (0xCC, btn, 0)
            self._last_temp_warmer = warmer
            await self._queue_cc(btn, CC_REQ, 0)
            self._last_temp_cmd_ts = time.monotonic()
            self._command_attempts += 1
            self._temp_check = TEMP_STATUS_WAIT
            return

        idx = int(getattr(self, "_temp_code_idx", 0)) % len(codes)
        mtype, btn, b6 = codes[idx]
        pkt = _build_cc(btn, self._assigned_channel, mtype, b6)
        scores = getattr(self, "_c6_score", {})
        locked = getattr(self, "_temp_locked_code", None) == (mtype, btn, b6)
        cur_b = getattr(self, "_temp_current_bucket", None)
        _LOGGER.warning(
            "Temp-Schritt C6 %s btn=0x%02X b6=0x%02X score=%s idx=%d/%d "
            "attempt=%d stall=%d locked=%s bucket=%s",
            "UP" if warmer else "DOWN",
            btn, b6, scores.get((mtype, btn, b6), 0),
            idx, len(codes), self._command_attempts + 1, stall, locked,
            self._bucket_label(cur_b) if cur_b is not None else "?",
        )
        self._last_temp_code = (mtype, btn, b6)
        self._last_temp_warmer = warmer
        await self._queue_raw(pkt)
        self._last_temp_cmd_ts = time.monotonic()
        self._command_attempts += 1
        self._temp_check = TEMP_STATUS_WAIT
        if not locked:
            self._temp_code_idx = idx + 1


    async def _handle_temp_feedback(self, status: dict) -> None:
        """Lock bei Erfolg, Blacklist bei GEGEN, Floor-Erkennung, autonomes Lernen."""
        if self._target_temp == NO_CHANGE_REQUESTED:
            return
        if self._temp_check > 0:
            self._temp_check -= 1
            return

        current = float(status["set_temp"])
        if abs(current - self._target_temp) < 0.3:
            _LOGGER.warning(
                "Soll-Temperatur erreicht: %.1f °C attempts=%d locked=%s",
                current, self._command_attempts,
                getattr(self, "_temp_locked_code", None),
            )
            self._target_temp = NO_CHANGE_REQUESTED
            self._temp_done.set()
            return

        if status.get("in_menu"):
            _LOGGER.warning("Temp: Panel im Menü – BTN_MENU")
            await self._queue_cc(BTN_MENU)
            await self._wait_pending_clear(timeout=2.0)
            self._temp_check = 2
            return

        if self._last_temp_seen is None:
            self._last_temp_seen = current

        prev = self._last_temp_seen
        delta = current - prev
        code = getattr(self, "_last_temp_code", None)
        if not hasattr(self, "_c6_score"):
            self._c6_score = {}

        # ── Range/Unit-Sprung-Filter ─────────────────────────────────────
        # Cameo 880: große Sprünge (33.5↔39.5) sind Decode-/Range-Artefakte.
        # Logs 2026-08-16: Climb bis ~33 funktioniert, darüber Oszillation.
        if abs(delta) >= TEMP_RANGE_JUMP_C:
            self._temp_jump_streak = getattr(self, "_temp_jump_streak", 0) + 1
            self._temp_stall_rounds = getattr(self, "_temp_stall_rounds", 0) + 1
            # Lock sofort lösen – der Code hat den Sprung ausgelöst/verstärkt
            if (
                isinstance(code, tuple)
                and len(code) == 3
                and code[0] == 0xC6
            ):
                self._temp_locked_code = None
                self._c6_score[code] = self._c6_score.get(code, 0) - 3
                # Nicht in den Pool des Artefakt-Werts schreiben
            _LOGGER.warning(
                "Temp-Feedback: großer Sprung ignoriert (Range/Unit-Artefakt, "
                "keine Wertung) %.1f → %.1f Δ=%.1f code=%s streak=%d",
                prev, current, delta, code, self._temp_jump_streak,
            )
            # Wenn der Sprung vom Ziel WEG führt: Anker behalten, Reading verwerfen
            anchor = self._temp_stable_anchor
            if anchor is None:
                anchor = prev
            err_cur = abs(current - self._target_temp)
            err_anchor = abs(anchor - self._target_temp)
            if err_cur > err_anchor + 0.4:
                # Artefakt weiter weg vom Ziel → last_seen nicht auf Artefakt setzen
                self._last_temp_seen = anchor
                guide = anchor
            else:
                self._last_temp_seen = current
                guide = current
            if abs(guide - self._target_temp) < 0.3:
                self._target_temp = NO_CHANGE_REQUESTED
                self._temp_done.set()
                return
            if self._command_attempts >= self._adaptive_temp_attempts():
                _LOGGER.error(
                    "set_temperature TIMEOUT | Ziel=%.1f got=%.1f attempts=%d",
                    self._target_temp, guide, self._command_attempts,
                )
                self._target_temp = NO_CHANGE_REQUESTED
                async with self._pending_lock:
                    self._pending.clear()
                self._temp_done.set()
                return
            # Nach mehreren Oszillationen: erst Range-HI versuchen (34↔28.5-Muster),
            # dann Code aus Anker-Bucket entfernen.
            if self._temp_jump_streak >= TEMP_OSCILLATION_LIMIT:
                if (
                    self._target_temp >= 33.5
                    and not getattr(self, "_temp_range_forced", False)
                ):
                    _LOGGER.warning(
                        "Temp-Oszillation streak=%d bei Ziel=%.1f → High-Range erzwingen",
                        self._temp_jump_streak, self._target_temp,
                    )
                    await self._force_temp_range_high()
                    self._temp_check = TEMP_STATUS_WAIT
                    await self._send_temp_step(self._target_temp > guide)
                    return
                if (
                    isinstance(code, tuple)
                    and len(code) == 3
                    and code[0] == 0xC6
                ):
                    wanted_up = self._target_temp > guide
                    self._remove_c6_from_bucket(wanted_up, code, anchor)
                    _LOGGER.warning(
                        "Temp-Oszillation: code=%s aus Bucket %s entfernt",
                        code, self._bucket_label(self._temp_bucket(anchor)),
                    )
                self._temp_jump_streak = 0
            self._temp_check = TEMP_STATUS_WAIT
            await self._send_temp_step(self._target_temp > guide)
            return

        # Sauberer Feedback-Pfad
        self._temp_jump_streak = 0
        self._temp_stable_anchor = current

        err_prev = abs(self._target_temp - prev)
        err_now = abs(self._target_temp - current)
        moved = abs(delta) >= 0.25
        warmer_needed = self._target_temp > current
        clean_step = abs(delta) <= TEMP_CLEAN_STEP_MAX + 0.01

        is_c6 = (
            isinstance(code, tuple)
            and len(code) == 3
            and isinstance(code[0], int)
            and code[0] == 0xC6
        )

        if moved and err_now < err_prev - 0.15:
            # Erfolg in Richtung Ziel
            self._temp_stall_rounds = 0
            self._temp_fail_on_code = 0
            if is_c6:
                step_bucket = self._temp_bucket(prev)
                direction = delta > 0
                # Nur echte ±0.5-Schritte stark belohnen (Logs: 32→33.5 war zu groß)
                score_add = 5 if clean_step else 1
                self._c6_score[code] = self._c6_score.get(code, 0) + score_add
                # In aktuellen Bucket legen + in Richtung nächster Bucket propagieren
                self._add_c6_to_bucket(direction, code, prev, score_add)
                next_temp = current if clean_step else (prev + (0.5 if direction else -0.5))
                self._add_c6_to_bucket(direction, code, next_temp, max(1, score_add // 2))
                if direction:
                    if code not in self._learned_c6_up:
                        self._learned_c6_up.append(code)
                    self._learned_c6_down = [c for c in self._learned_c6_down if c != code]
                    self._temp_blacklist_down.discard(code)
                    self._temp_blacklist_up.discard(code)
                else:
                    if code not in self._learned_c6_down:
                        self._learned_c6_down.append(code)
                    self._learned_c6_up = [c for c in self._learned_c6_up if c != code]
                    self._temp_blacklist_up.discard(code)
                    self._temp_blacklist_down.discard(code)
                # STRONG-LOCK nur bei sauberem 0.5-Schritt
                if (
                    clean_step
                    and self._c6_score[code] >= TEMP_STRONG_LOCK_SCORE
                ):
                    self._temp_locked_code = code
                    _LOGGER.warning(
                        "Temp-C6 STRONG-LOCK code=%s bucket=%s score=%d Δ=%.1f",
                        code, self._bucket_label(step_bucket),
                        self._c6_score[code], delta,
                    )
                elif abs(delta) >= TEMP_JUMP_UNLOCK:
                    # Großer Fortschritt ohne Clean-Step → kein Lock behalten
                    if self._temp_locked_code == code:
                        self._temp_locked_code = None
                        _LOGGER.warning(
                            "Temp-Lock verweigert (Δ=%.1f zu groß für 1 Schritt): %s",
                            delta, code,
                        )
            self._temp_steps_done = getattr(self, "_temp_steps_done", 0) + 1
            _LOGGER.warning(
                "Temp-Fortschritt: %.1f → %.1f (Ziel %.1f) code=%s score=%s%s | Pause",
                prev, current, self._target_temp, code,
                self._c6_score.get(code, 0) if is_c6 else "-",
                " LOCK" if (is_c6 and self._temp_locked_code == code) else "",
            )
            self._last_temp_seen = current
            self._temp_check = TEMP_STATUS_WAIT
            await asyncio.sleep(0.8)
            if self._target_temp == NO_CHANGE_REQUESTED:
                return
            if abs(current - self._target_temp) < 0.3:
                self._target_temp = NO_CHANGE_REQUESTED
                self._temp_done.set()
                return
            await self._send_temp_step(self._target_temp > current)
            return

        if moved and err_now > err_prev + 0.15:
            # GEGEN → Unlock + per-Bucket-Blacklist (nicht global löschen!)
            # Ein Code der bei 28° falsch läuft kann bei 36° korrekt sein.
            self._temp_stall_rounds = getattr(self, "_temp_stall_rounds", 0) + 1
            self._temp_locked_code = None
            if is_c6:
                self._c6_score[code] = self._c6_score.get(code, 0) - 6
                wanted_warmer = bool(getattr(self, "_last_temp_warmer", warmer_needed))
                self._remove_c6_from_bucket(wanted_warmer, code, prev)
                # Nur bei wiederholtem GEGEN im selben Bucket global blacklisten
                bkey = (
                    1 if wanted_warmer else 0,
                    self._temp_bucket(prev),
                    code[1],
                    code[2],
                )
                fails = abs(self._c6_bucket_score.get(bkey, 0))
                if fails >= 12:
                    if wanted_warmer:
                        self._temp_blacklist_up.add(code)
                        self._learned_c6_up = [
                            c for c in self._learned_c6_up if c != code
                        ]
                    else:
                        self._temp_blacklist_down.add(code)
                        self._learned_c6_down = [
                            c for c in self._learned_c6_down if c != code
                        ]
            _LOGGER.warning(
                "Temp GEGEN: %.1f → %.1f code=%s bucket=%s stall=%d",
                prev, current, code,
                self._bucket_label(self._temp_bucket(prev)),
                self._temp_stall_rounds,
            )
            self._last_temp_seen = current
            self._temp_check = TEMP_STATUS_WAIT + 2
            if abs(delta) >= 1.5:
                await asyncio.sleep(1.0)
        else:
            # keine Bewegung
            self._temp_stall_rounds = getattr(self, "_temp_stall_rounds", 0) + 1
            if is_c6:
                self._c6_score[code] = self._c6_score.get(code, 0) - 1
                if getattr(self, "_temp_locked_code", None) == code:
                    self._temp_fail_on_code = getattr(self, "_temp_fail_on_code", 0) + 1
                    if self._temp_fail_on_code >= 2:
                        _LOGGER.warning("Temp-Lock gelöst nach 2 Fehlversuchen: %s", code)
                        self._temp_locked_code = None
                        self._temp_fail_on_code = 0

            # Spa-Floor: Ziel unter Minimum (~28°C High-Range)
            if (
                not warmer_needed
                and not moved
                and self._temp_stall_rounds >= TEMP_FLOOR_PROBE
                and current <= TEMP_SPA_FLOOR_C + 1.5
                and self._target_temp < current - 0.2
            ):
                _LOGGER.warning(
                    "Temp-Floor erreicht: ist=%.1f Ziel=%.1f – akzeptiere Minimum",
                    current, self._target_temp,
                )
                self._target_temp = NO_CHANGE_REQUESTED
                self._temp_done.set()
                return

        if self._command_attempts >= self._adaptive_temp_attempts():
            # Nahe am Floor und Ziel darunter → als Floor werten, kein TIMEOUT-Fehler
            if (
                not warmer_needed
                and current <= TEMP_SPA_FLOOR_C + 1.5
                and self._target_temp < current
            ):
                _LOGGER.warning(
                    "Temp-Floor (Timeout): ist=%.1f Ziel=%.1f – akzeptiere",
                    current, self._target_temp,
                )
            else:
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
        self._temp_check = TEMP_STATUS_WAIT


    async def _try_direct_set(self, target: float) -> None:
        """MSG 0x20 Direct-Set – primäre Methode (kein Rolling-Code nötig)."""
        ch = self._assigned_channel or CMD_CHANNEL
        pkt = _build_set_temp(ch, target)
        _LOGGER.warning(
            "Direct-Set 0x20 | Ziel=%.1f ch=0x%02X pkt=%s",
            target, ch, pkt.hex(" "),
        )
        await self._queue_raw(pkt)
        await self._wait_pending_clear(timeout=4.0)
        await asyncio.sleep(1.2)


    async def _send_light_step(self, color: bool = False) -> None:
        """Licht-Schritt einreihen (nicht blockieren).

        C7 vom Panel funktioniert, von uns trotz CTS nicht.
        Deshalb mehrere Varianten testen:
          0-3:   C7 encrypted (Panel-Format)
          4-6:   CC Klartext 0x2F/0x33
          7-9:   CC Klartext 241 (Balboa Standard)
          10-12: encrypted CC 0x2F/0x33
        """
        if self._light_attempt >= 14:
            if not getattr(self, "_light_exhausted_logged", False):
                _LOGGER.error("Licht-Versuche erschöpft – Abbruch")
                self._light_exhausted_logged = True
            self._target_light_brightness = LIGHT_NO_CHANGE
            self._target_light_mode = LIGHT_NO_CHANGE
            async with self._pending_lock:
                self._pending.clear()
            self._light_done.set()
            return

        async with self._pending_lock:
            if self._pending:
                return

        self._assigned_channel = CMD_CHANNEL
        attempt = self._light_attempt
        ch = CMD_CHANNEL

        if color:
            _LOGGER.warning("Licht COLOR CC-F2 attempt=%d", attempt + 1)
            await self._queue_cc(0xF2, CC_REQ, 0x00)
        elif attempt < 4:
            key_byte = ((attempt + 1) * 0x17 + 0x04) & 0xFF
            pkt = _build_c7(LIGHT_C7_BTN, LIGHT_C7_B6, ch, key_byte=key_byte)
            _LOGGER.warning(
                "Licht C7-ENC key=0x%02X attempt=%d pkt=%s",
                key_byte, attempt + 1, pkt.hex(" "),
            )
            await self._queue_raw(pkt)
        elif attempt < 7:
            _LOGGER.warning("Licht CC-2F33 attempt=%d", attempt + 1)
            await self._queue_cc(LIGHT_C7_BTN, CC_REQ, LIGHT_C7_B6)
        elif attempt < 10:
            _LOGGER.warning("Licht CC-241 attempt=%d", attempt + 1)
            await self._queue_cc(BTN_LIGHT, CC_REQ, 0)
        else:
            key_byte = ((attempt + 1) * 0x13) & 0xFF
            pkt = _build_cc_encrypted(
                LIGHT_C7_BTN, ch, CC_REQ, LIGHT_C7_B6, key_byte=key_byte
            )
            _LOGGER.warning(
                "Licht CC-ENC 2F/33 key=0x%02X attempt=%d pkt=%s",
                key_byte, attempt + 1, pkt.hex(" "),
            )
            await self._queue_raw(pkt)

        self._light_attempt += 1

    async def _handle_light_feedback(self, lights: dict) -> None:
        """Nur Erfolg erkennen – Senden macht die set_light-Schleife (nicht Receiver)."""
        if self._target_light_mode != LIGHT_NO_CHANGE:
            if lights.get("mode_raw") == self._target_light_mode:
                _LOGGER.warning(
                    "Licht-Modus erreicht: %s (raw=%s)",
                    lights.get("mode"),
                    lights["mode_raw"],
                )
                self._target_light_mode = LIGHT_NO_CHANGE
                if self._target_light_brightness == LIGHT_NO_CHANGE:
                    self._light_done.set()
            return

        if self._target_light_brightness == LIGHT_NO_CHANGE:
            return

        cur = int(lights.get("brightness_raw") or 0)
        tgt = int(self._target_light_brightness)
        if cur == tgt:
            _LOGGER.warning(
                "Licht-Helligkeit erreicht: %s (Ziel=%s) attempts=%d",
                cur, tgt, self._light_attempt,
            )
            self._target_light_brightness = LIGHT_NO_CHANGE
            self._light_done.set()

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

    async def _force_temp_range_high(self) -> None:
        """High-Range erzwingen – Logs: über ~33–34°C sonst 34↔28.5-Oszillation.

        Cameo speichert Low- und High-Range-Soll getrennt. Ohne High-Range
        enden UP-Schritte an der Low-Range-Grenze und springen auf den
        Low-Sollwert (~28.5) zurück. Deshalb vor Zielen ≥33.5 und bei
        erkannter Oszillation explizit RANGE HI senden.
        """
        _LOGGER.warning(
            "Temp-Range → HIGH (CC %d / alt %d) – Ziel über Low-Range-Grenze",
            BTN_TEMP_RANGE_HI, TEMP_RANGE_HI_CC_BTN,
        )
        # Zwei Varianten: klassischer Button + Cameo-CC-Variante aus dem Code
        await self._queue_cc(BTN_TEMP_RANGE_HI)
        await self._wait_pending_clear(timeout=2.0)
        await asyncio.sleep(0.4)
        await self._queue_cc(TEMP_RANGE_HI_CC_BTN, CC_REQ, TEMP_RANGE_HI_CC_B6)
        await self._wait_pending_clear(timeout=2.0)
        await asyncio.sleep(0.6)
        self._temp_jump_streak = 0
        self._temp_range_forced = True

    async def _ensure_temp_range(self, target: float, snap: dict | None = None) -> None:
        """High-Range anfordern wenn Ziel über der typischen Low-Range-Grenze liegt."""
        if target < 33.5:
            return
        if getattr(self, "_temp_range_forced", False):
            return
        await self._force_temp_range_high()
        if snap:
            # Anker nach Range-Switch neu setzen
            st = snap.get("set_temp")
            if st is not None:
                self._temp_stable_anchor = float(st)
                self._last_temp_seen = float(st)

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
        """Solltemperatur steuern: C6-Learn → Klartext-CC → Direct-Set."""
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
            self._temp_stall_rounds = 0
            self._temp_progress_at = 0
            self._temp_code_idx = 0
            self._temp_fail_on_code = 0
            self._temp_exploration_round = 0
            self._temp_jump_streak = 0
            self._temp_range_forced = False
            self._temp_target_bucket = self._temp_bucket(target)
            self._temp_current_bucket = self._temp_bucket(float(snap["set_temp"]))
            self._temp_stable_anchor = float(snap["set_temp"])
            # Lock behalten wenn Richtung passt, sonst neu lernen
            locked = getattr(self, "_temp_locked_code", None)
            warmer = target > snap["set_temp"]
            if locked and locked in self._temp_blacklist(warmer):
                self._temp_locked_code = None
            # Alte per-Bucket-Blacklist für den Start-Bucket leicht lockern
            start_b = self._temp_current_bucket
            stale = [
                k for k in self._c6_bucket_blacklist
                if k[1] == start_b and k[0] == (1 if warmer else 0)
            ]
            for k in stale[:4]:
                self._c6_bucket_blacklist.discard(k)
            # Beim Hochlaufen: bewährte Low-Range-UP-Codes in current+next legen
            if warmer and float(snap["set_temp"]) < 33.5:
                for entry in list(self._learned_c6_up)[-6:]:
                    self._add_c6_to_bucket(True, entry, float(snap["set_temp"]), 0)
                    self._add_c6_to_bucket(
                        True, entry, min(target, float(snap["set_temp"]) + 2.0), 0
                    )
            self._last_temp_seen = float(snap["set_temp"])
            self._debug_cmd = True
            self._target_temp = target

            cur_pool = len(
                self._c6_buckets["up" if warmer else "down"].get(
                    self._temp_current_bucket, []
                )
            )
            _LOGGER.warning(
                "set_temperature START | Ziel=%.1f aktuell=%.1f kanal=0x%02X "
                "| Modus=C6-Bucket/Learn→CC→0x20 pool_up=%d pool_down=%d "
                "cur_bucket=%s tgt_bucket=%s cur_pool=%d max_attempts=%d lock=%s",
                target, snap["set_temp"], ch,
                len(self._learned_c6_up), len(self._learned_c6_down),
                self._bucket_label(self._temp_current_bucket),
                self._bucket_label(self._temp_target_bucket),
                cur_pool,
                self._adaptive_temp_attempts(target),
                self._temp_locked_code,
            )

            # Cameo 880 (40A): Jet-Pumpen aus, sonst ignoriert Panel Temp-Tasten
            try:
                await self._ensure_pumps_off_for_heating()
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning("Pumpen-Aus vor Temp fehlgeschlagen: %s", exc)

            # High-Range VOR dem Hochlaufen – sonst endet UP bei ~33–34 und
            # springt auf den Low-Range-Soll (~28.5) zurück (Log-Muster 34↔28.5).
            try:
                await self._ensure_temp_range(target, snap)
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning("Temp-Range-Switch fehlgeschlagen: %s", exc)

            # Menü verlassen falls nötig
            snap2 = await self._status_snapshot()
            if snap2 and snap2.get("in_menu"):
                await self._queue_cc(BTN_MENU)
                await self._wait_pending_clear()
                await asyncio.sleep(0.5)

            warmer = target > (snap2 or snap)["set_temp"]
            await self._send_temp_step(warmer)
            self._temp_check = CHECKS_BEFORE_RETRY

            try:
                await asyncio.wait_for(self._temp_done.wait(), timeout=75.0)
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
            self._light_exhausted_logged = False
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
                self._target_light_mode = mode
                # Falls aus: Helligkeit mit anwerfen (Steuerschleife schaltet zuerst ein)
                lights = await self._lights_snapshot()
                if not lights or int(lights.get("brightness_raw") or 0) == 0:
                    self._target_light_brightness = 100
            elif on is False or (brightness_pct is not None and brightness_pct <= 0):
                self._target_light_brightness = 0
            elif brightness_pct is not None:
                self._target_light_brightness = _brightness_step(brightness_pct)
            elif on is True:
                self._target_light_brightness = 100
            else:
                return

            # Aktive Steuerung hier (nicht im Receiver): einreihen → CTS abwarten → Status prüfen
            tgt = self._target_light_brightness
            mode_tgt = self._target_light_mode
            deadline = time.monotonic() + 45.0
            while time.monotonic() < deadline:
                lights = await self._lights_snapshot()
                if not lights:
                    await self._send_light_step(color=False)
                    await self._wait_pending_clear(timeout=2.5)
                    await asyncio.sleep(0.6)
                    continue

                cur = int(lights.get("brightness_raw") or 0)
                if mode_tgt != LIGHT_NO_CHANGE:
                    if lights.get("mode_raw") == mode_tgt:
                        self._target_light_mode = LIGHT_NO_CHANGE
                        self._light_done.set()
                        break
                    # ggf. erst einschalten
                    if cur == 0:
                        await self._send_light_step(color=False)
                    else:
                        await self._send_light_step(color=True)
                elif tgt != LIGHT_NO_CHANGE:
                    if cur == tgt:
                        _LOGGER.warning(
                            "Licht-Helligkeit erreicht: %s (Ziel=%s) attempts=%d",
                            cur, tgt, self._light_attempt,
                        )
                        self._target_light_brightness = LIGHT_NO_CHANGE
                        self._light_done.set()
                        break
                    await self._send_light_step(color=False)
                else:
                    self._light_done.set()
                    break

                # CTS-Fenster abwarten (wir sind NICHT im Receiver – blockieren ok)
                await self._wait_pending_clear(timeout=2.5)
                await asyncio.sleep(0.7)  # Board braucht Zeit für CA-Update

            try:
                if self._light_done.is_set():
                    _LOGGER.info(
                        "set_light OK | sent=%d queued=%d last_cc=%s attempts=%d",
                        self._cc_sent,
                        self._cc_queued,
                        self._last_cc_hex,
                        self._light_attempt,
                    )
                else:
                    lights = await self._lights_snapshot()
                    state = "an" if lights and lights.get("on") else "aus"
                    _LOGGER.error(
                        "set_light TIMEOUT | aktuell=%s bright=%s mode=%s | "
                        "sent=%d queued=%d last_cc=%s kanal=0x%02X",
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
                    )
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
