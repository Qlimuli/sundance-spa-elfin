"""Balboa protocol implementation for Sundance Spa via EW11 RS485-to-TCP bridge.

Protokoll-Referenz: balboa_worldwide_app / pybalboa / bwalink
Frame-Format:
  0x7E | LENGTH | SRC | TYPE_HI | TYPE_LO | PAYLOAD... | CRC | 0x7E
  LENGTH = Anzahl Bytes ab LENGTH bis CRC (inkl. beider).
  => Gesamtframe-Länge = LENGTH + 2  (Start + End-Delimiter)

WICHTIG: 0x7E kann als CRC oder Payload-Byte auftreten (kein Byte-Stuffing).
Der Parser verwendet deshalb LENGTH als primäre Frame-Grenze, nicht den
End-Delimiter als Suchanker.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Callable

_LOGGER = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Protokoll-Konstanten
# ─────────────────────────────────────────────────────────────────────────────
MSG_DELIM = 0x7E          # Start- UND End-Delimiter
SRC_WIFI  = 0x0A          # Quelladresse für WiFi-Clients

# Nachrichten-Typen  (2 Byte: HIGH=0xAF/0xBF, LOW=Typ-ID)
# Empfangen (Spa → Client)
MT_STATUS   = bytes([0xAF, 0x13])  # Statusupdate  ~3.3 Hz
MT_FILTER   = bytes([0xAF, 0x23])  # Filter-Zyklen
MT_INFO     = bytes([0xAF, 0x24])  # System-Info
MT_SETTINGS = bytes([0xAF, 0x25])  # Einstellungen
MT_SETUP    = bytes([0xAF, 0x26])  # Setup-Parameter
MT_CONFIG   = bytes([0xAF, 0x2E])  # Konfiguration (Control Config 2)
MT_READY    = bytes([0xAF, 0x14])  # CTS – bereit zum Empfangen
MT_NTS      = bytes([0xAF, 0x06])  # Nothing-to-Send

# Senden (Client → Spa)
MT_CONFIG_REQ = bytes([0xBF, 0x04])  # Konfiguration anfordern
MT_TOGGLE     = bytes([0xBF, 0x11])  # Element ein/ausschalten
MT_SET_TEMP   = bytes([0xBF, 0x20])  # Zieltemperatur setzen
MT_SET_TIME   = bytes([0xBF, 0x21])  # Uhrzeit setzen
MT_SET_SCALE  = bytes([0xBF, 0x27])  # Temperatureinheit setzen

# Toggle-Codes
class ToggleItem(IntEnum):
    PUMP1      = 0x04
    PUMP2      = 0x05
    PUMP3      = 0x06
    PUMP4      = 0x07
    PUMP5      = 0x08
    PUMP6      = 0x09
    LIGHT1     = 0x11
    LIGHT2     = 0x12
    AUX1       = 0x16
    AUX2       = 0x17
    MISTER     = 0x0E
    BLOWER     = 0x0C
    HOLD       = 0x3C
    TEMP_RANGE = 0x50
    HEAT_MODE  = 0x51


class HeatMode(IntEnum):
    READY         = 0
    REST          = 1
    READY_IN_REST = 2


class HeatState(IntEnum):
    OFF          = 0
    HEATING      = 1
    HEAT_WAITING = 2


class TempRange(IntEnum):
    LOW  = 0
    HIGH = 1


# ─────────────────────────────────────────────────────────────────────────────
# Datenklassen
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SpaStatus:
    """Aktueller Spa-Status – direkt aus dem Statusframe geparst."""
    current_temp: float | None = None
    target_temp:  float | None = None
    temp_scale_celsius: bool   = False
    temp_range:  TempRange     = TempRange.HIGH
    heat_mode:   HeatMode      = HeatMode.READY
    heating:     bool          = False
    pump1: int = 0
    pump2: int = 0
    pump3: int = 0
    pump4: int = 0
    pump5: int = 0
    pump6: int = 0
    blower: int  = 0
    light1: bool = False
    light2: bool = False
    mister: bool = False
    aux1:   bool = False
    aux2:   bool = False
    circ_pump:      bool = False
    filter1_running: bool = False
    filter2_running: bool = False
    hour:      int  = 0
    minute:    int  = 0
    clock_24hr: bool = True
    priming:   bool = False
    hold_mode: bool = False


@dataclass
class SpaConfig:
    """Spa-Konfiguration aus dem Config-Frame oder Defaults."""
    model:       str       = ""
    software_id: str       = ""
    pump_count:  int       = 2
    pump_speeds: list[int] = field(default_factory=lambda: [2, 2, 0, 0, 0, 0])
    has_blower:  bool      = False
    blower_speeds: int     = 0
    has_mister:  bool      = False
    has_aux1:    bool      = False
    has_aux2:    bool      = False
    has_circ_pump: bool    = True
    light_count: int       = 1


# ─────────────────────────────────────────────────────────────────────────────
# CRC
# ─────────────────────────────────────────────────────────────────────────────

def _crc8(data: bytes) -> int:
    """Balboa CRC-8: Poly=0x07, Init=0x02, XorOut=0x02."""
    crc = 0x02
    for byte in data:
        for i in range(8):
            bit = crc & 0x80
            crc = ((crc << 1) & 0xFF) | ((byte >> (7 - i)) & 0x01)
            if bit:
                crc ^= 0x07
    for _ in range(8):
        bit = crc & 0x80
        crc = (crc << 1) & 0xFF
        if bit:
            crc ^= 0x07
    return crc ^ 0x02


# ─────────────────────────────────────────────────────────────────────────────
# Frame-Builder
# ─────────────────────────────────────────────────────────────────────────────

def _build_frame(msg_type: bytes, payload: bytes = b"") -> bytes:
    """
    Baut einen vollständigen Balboa-Frame.
    content  = SRC + TYPE(2) + PAYLOAD
    length   = len(content) + 2   (+1 für length-Byte selbst, +1 für CRC)
    frame    = 0x7E | length | content | CRC | 0x7E
    """
    content = bytes([SRC_WIFI]) + msg_type + payload
    length  = len(content) + 2          # length + CRC zählen mit
    body    = bytes([length]) + content
    crc     = _crc8(body)
    return bytes([MSG_DELIM]) + body + bytes([crc, MSG_DELIM])


# ─────────────────────────────────────────────────────────────────────────────
# SpaClient
# ─────────────────────────────────────────────────────────────────────────────

class SpaClient:
    """TCP-Client für Balboa-Spa via EW11-Transparent-Bridge."""

    def __init__(self, host: str, port: int = 8899) -> None:
        self._host  = host
        self._port  = port
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._connected = False

        self._status = SpaStatus()
        self._config = SpaConfig()
        self._config_loaded = False

        self._callbacks: list[Callable[[], None]] = []
        self._receive_task: asyncio.Task | None   = None
        self._send_lock = asyncio.Lock()

        # Byte-Buffer – accumulates raw TCP bytes
        self._buf = bytearray()

        # Synchronisation
        self._first_status_parsed = asyncio.Event()
        self._config_received     = asyncio.Event()
        self._heat_mode_changed   = asyncio.Event()

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def host(self) -> str:
        return self._host

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def status(self) -> SpaStatus:
        return self._status

    @property
    def config(self) -> SpaConfig:
        return self._config

    @property
    def model(self) -> str:
        return self._config.model

    @property
    def temperature(self) -> float | None:
        return self._status.current_temp

    @property
    def target_temperature(self) -> float | None:
        return self._status.target_temp

    @property
    def temperature_unit_celsius(self) -> bool:
        return self._status.temp_scale_celsius

    @property
    def temperature_minimum(self) -> float:
        if self._status.temp_scale_celsius:
            return 10.0 if self._status.temp_range == TempRange.LOW else 26.0
        return 50.0 if self._status.temp_range == TempRange.LOW else 80.0

    @property
    def temperature_maximum(self) -> float:
        if self._status.temp_scale_celsius:
            return 37.0 if self._status.temp_range == TempRange.LOW else 40.0
        return 99.0 if self._status.temp_range == TempRange.LOW else 104.0

    @property
    def heat_mode(self) -> HeatMode:
        return self._status.heat_mode

    @property
    def heat_state(self) -> HeatState:
        return HeatState.HEATING if self._status.heating else HeatState.OFF

    # ── Callbacks ─────────────────────────────────────────────────────────────

    def add_update_callback(self, cb: Callable[[], None]) -> Callable[[], None]:
        """Callback registrieren; gibt Unsubscribe-Funktion zurück."""
        self._callbacks.append(cb)
        def _remove():
            try:
                self._callbacks.remove(cb)
            except ValueError:
                pass
        return _remove

    def _notify(self) -> None:
        for cb in list(self._callbacks):
            try:
                cb()
            except Exception as exc:
                _LOGGER.error("Update-Callback Fehler: %s", exc)

    # ── Verbindung ────────────────────────────────────────────────────────────

    async def connect(self) -> bool:
        """TCP-Verbindung zum EW11 aufbauen."""
        try:
            _LOGGER.info("Verbinde mit %s:%d …", self._host, self._port)
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self._host, self._port),
                timeout=10,
            )
        except asyncio.TimeoutError:
            _LOGGER.error("Verbindungs-Timeout zu %s:%d", self._host, self._port)
            return False
        except OSError as exc:
            _LOGGER.error("Verbindungsfehler: %s", exc)
            return False

        self._connected = True
        self._buf.clear()
        self._first_status_parsed.clear()
        self._config_received.clear()
        self._heat_mode_changed.clear()
        self._config_loaded = False

        self._receive_task = asyncio.create_task(
            self._receive_loop(), name=f"spa_recv_{self._host}"
        )
        _LOGGER.info("Verbunden mit %s:%d", self._host, self._port)
        return True

    async def disconnect(self) -> None:
        """Verbindung sauber trennen."""
        self._connected = False
        if self._receive_task and not self._receive_task.done():
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
        self._receive_task = None
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
            self._writer = None
            self._reader = None
        _LOGGER.info("Getrennt von %s", self._host)

    async def async_configuration_loaded(self) -> bool:
        """
        Wartet auf den ersten vollständig geparsten Statusframe,
        danach wird die Konfiguration vom Spa angefordert.
        Timeout-Verantwortung liegt beim Aufrufer (__init__.py).
        """
        # ── Schritt 1: Ersten Statusframe abwarten (max 30 s) ──────────────
        try:
            await asyncio.wait_for(self._first_status_parsed.wait(), timeout=30.0)
        except asyncio.TimeoutError:
            _LOGGER.error(
                "Timeout waiting for first status frame – "
                "check EW11 mode (must be TCP Server, NOT Modbus)"
            )
            return False

        _LOGGER.info("Erster Statusframe empfangen und geparst ✓")

        # ── Schritt 2: Konfiguration anfordern (best-effort, max 8 s) ──────
        try:
            for cfg_type in (1, 2, 3):
                await self._send(_build_frame(MT_CONFIG_REQ, bytes([cfg_type, 0x00, 0x00])))
                await asyncio.sleep(0.25)

            await asyncio.wait_for(self._config_received.wait(), timeout=8.0)
            _LOGGER.info("Spa-Konfiguration geladen ✓")
        except asyncio.TimeoutError:
            _LOGGER.warning(
                "Keine Config-Antwort – verwende Sundance Cameo 880 Defaults"
            )
            self._apply_cameo_880_defaults()

        self._config_loaded = True
        return True

    def _apply_cameo_880_defaults(self) -> None:
        self._config.model        = "Sundance Cameo 880"
        self._config.pump_count   = 3
        self._config.pump_speeds  = [2, 2, 1, 0, 0, 0]
        self._config.has_blower   = False
        self._config.has_circ_pump = True
        self._config.light_count  = 1

    # ── Receive-Loop ──────────────────────────────────────────────────────────

    async def _receive_loop(self) -> None:
        """
        Liest kontinuierlich Bytes vom EW11 und übergibt sie dem Frame-Parser.

        FIX: Kein harter Timeout mehr im read()-Aufruf.
             asyncio.StreamReader.read() blockiert nur bis Daten kommen
             oder die Verbindung geschlossen wird – kein künstlicher 60s-Rauswurf.
        """
        assert self._reader is not None
        try:
            while self._connected:
                try:
                    chunk = await self._reader.read(4096)
                except asyncio.CancelledError:
                    break
                except Exception as exc:
                    _LOGGER.error("Lesefehler: %s", exc)
                    break

                if not chunk:
                    _LOGGER.warning("EW11 hat Verbindung geschlossen")
                    break

                _LOGGER.debug("RX %d Bytes: %s", len(chunk), chunk.hex())
                self._buf.extend(chunk)
                self._parse_frames()

        finally:
            self._connected = False
            self._notify()
            _LOGGER.info("Receive-Loop beendet")

    # ── Frame-Parser ──────────────────────────────────────────────────────────

    def _parse_frames(self) -> None:
        """
        Extrahiert alle vollständigen Frames aus self._buf.

        Strategie (robust gegen 0x7E in Payload/CRC):
        1. Suche Start-Delimiter 0x7E ab der aktuellen Position.
        2. Lese LENGTH-Byte (buf[pos+1]).
        3. Prüfe ob genug Bytes vorhanden: pos + 1 + LENGTH + 1.
        4. Extrahiere den Frame, verifiziere CRC, übergib ihn.
        5. End-Delimiter wird NUR zur Plausibilitätsprüfung genutzt,
           ist aber NICHT der Suchanker für den nächsten Frame.
        """
        buf  = self._buf
        pos  = 0
        size = len(buf)

        while pos < size:
            # ── 1. Start-Delimiter suchen ────────────────────────────────────
            if buf[pos] != MSG_DELIM:
                pos += 1
                continue

            # ── 2. LENGTH-Byte lesen ─────────────────────────────────────────
            if pos + 1 >= size:
                break  # noch kein LENGTH-Byte → warten

            length = buf[pos + 1]

            # Plausibilitätscheck: gültige Länge 5..255 (min: SRC+TYPE(2)+CRC = 4, +length = 5)
            # 0x7E als LENGTH ist ungültig (wäre weiterer Delimiter)
            if length < 5 or length == MSG_DELIM:
                # Kein gültiger Frame hier – ein Byte weiterrücken
                pos += 1
                continue

            # ── 3. Vollständiger Frame vorhanden? ────────────────────────────
            # Gesamtlänge = 1 (Start) + LENGTH + 1 (End)
            frame_len = length + 2
            if pos + frame_len > size:
                break  # Unvollständig → warten auf mehr Daten

            # ── 4. Frame extrahieren ─────────────────────────────────────────
            frame = bytes(buf[pos : pos + frame_len])

            # End-Delimiter prüfen (Plausibilität, kein harter Abbruch)
            if frame[-1] != MSG_DELIM:
                _LOGGER.debug(
                    "Frame bei pos %d: fehlendes End-Delimiter "
                    "(last=0x%02X) – trotzdem CRC prüfen",
                    pos, frame[-1],
                )

            # ── 5. CRC prüfen ────────────────────────────────────────────────
            # CRC wird über alles von LENGTH bis CRC-1 berechnet
            body         = frame[1:-2]   # LENGTH + SRC + TYPE + PAYLOAD
            expected_crc = _crc8(body)
            actual_crc   = frame[-2]

            if expected_crc != actual_crc:
                _LOGGER.debug(
                    "CRC-Fehler bei pos %d: erwartet=0x%02X, ist=0x%02X "
                    "– Frame verwerfen, 1 Byte weiterrücken",
                    pos, expected_crc, actual_crc,
                )
                pos += 1
                continue

            # ── 6. Gültigen Frame verarbeiten ────────────────────────────────
            pos += frame_len
            self._dispatch(frame)

        # Verarbeitete Bytes aus Buffer entfernen
        if pos > 0:
            self._buf = self._buf[pos:]

    def _dispatch(self, frame: bytes) -> None:
        """
        Einen verifizierten Frame in Felder zerlegen und verarbeiten.
        frame = 0x7E | LENGTH | SRC | TYPE_HI | TYPE_LO | PAYLOAD… | CRC | 0x7E
        """
        # SRC  = frame[2]
        # TYPE = frame[3:5]
        # PAYLOAD = frame[5:-2]
        if len(frame) < 7:
            return

        msg_type = frame[3:5]
        payload  = frame[5:-2]

        _LOGGER.debug("Frame: type=%s payload(%d)=%s",
                      msg_type.hex(), len(payload), payload.hex())

        if msg_type == MT_STATUS:
            self._parse_status(payload)
            # Event NACH vollständigem Parse setzen
            self._first_status_parsed.set()
            self._notify()

        elif msg_type == MT_CONFIG:
            self._parse_config(payload)
            self._config_received.set()

        elif msg_type == MT_INFO:
            self._parse_info(payload)

        elif msg_type == MT_FILTER:
            _LOGGER.debug("Filter-Zyklen: %s", payload.hex())

        elif msg_type in (MT_READY, MT_NTS):
            pass  # CTS / Nothing-to-send – keine Aktion nötig

        else:
            _LOGGER.debug("Unbekannter Frame-Typ: %s", msg_type.hex())

    # ── Status-Parser ─────────────────────────────────────────────────────────

    def _parse_status(self, p: bytes) -> None:
        """
        Statusframe-Payload parsen.
        Mindestlänge: 20 Bytes.
        FIX: temp_scale_celsius wird als ERSTES gelesen (aus flags9),
             DANACH werden Temperaturen konvertiert.
        """
        if len(p) < 20:
            _LOGGER.warning("Statusframe zu kurz: %d Bytes", len(p))
            return

        prev_heat_mode = self._status.heat_mode

        # ── Byte 9: Flags (Skala, Filter, Uhr) – ZUERST lesen ───────────────
        flags9 = p[9]
        self._status.temp_scale_celsius = bool(flags9 & 0x01)
        self._status.clock_24hr         = bool(flags9 & 0x02)
        self._status.filter1_running    = bool(flags9 & 0x04)
        self._status.filter2_running    = bool(flags9 & 0x08)

        # ── Byte 0: Hold-Mode ────────────────────────────────────────────────
        self._status.hold_mode = bool(p[0] & 0x05)

        # ── Byte 1: Priming ──────────────────────────────────────────────────
        self._status.priming = p[1] == 0x01

        # ── Byte 2: Aktuelle Temperatur ──────────────────────────────────────
        if p[2] != 0xFF:
            self._status.current_temp = (
                p[2] / 2.0 if self._status.temp_scale_celsius else float(p[2])
            )
        else:
            self._status.current_temp = None  # Sensor initialisiert sich noch

        # ── Bytes 3–4: Uhrzeit ───────────────────────────────────────────────
        self._status.hour   = p[3]
        self._status.minute = p[4]

        # ── Byte 5: Heat-Mode (Bits 0–1) ────────────────────────────────────
        hm_raw = p[5] & 0x03
        self._status.heat_mode = HeatMode(hm_raw) if hm_raw <= 2 else HeatMode.READY

        # ── Byte 10: Heat-State und Temp-Range ───────────────────────────────
        flags10 = p[10]
        self._status.heating    = bool(flags10 & 0x30)
        self._status.temp_range = TempRange.HIGH if (flags10 & 0x04) else TempRange.LOW

        # ── Byte 11: Pumpen 1–4 ──────────────────────────────────────────────
        flags11 = p[11]
        self._status.pump1 = flags11 & 0x03
        self._status.pump2 = (flags11 >> 2) & 0x03
        self._status.pump3 = (flags11 >> 4) & 0x03
        self._status.pump4 = (flags11 >> 6) & 0x03

        # ── Byte 12: Pumpen 5–6 ──────────────────────────────────────────────
        flags12 = p[12]
        self._status.pump5 = flags12 & 0x03
        self._status.pump6 = (flags12 >> 2) & 0x03

        # ── Byte 13: Zirkulationspumpe + Blower ──────────────────────────────
        flags13 = p[13]
        self._status.circ_pump = bool(flags13 & 0x02)
        self._status.blower    = (flags13 >> 2) & 0x03

        # ── Byte 14: Lichter ─────────────────────────────────────────────────
        flags14 = p[14]
        self._status.light1 = bool(flags14 & 0x03)
        self._status.light2 = bool((flags14 >> 2) & 0x03)

        # ── Byte 15: Mister + Aux ────────────────────────────────────────────
        flags15 = p[15]
        self._status.mister = bool(flags15 & 0x01)
        self._status.aux1   = bool(flags15 & 0x08)
        self._status.aux2   = bool(flags15 & 0x10)

        # ── Byte 20: Zieltemperatur ───────────────────────────────────────────
        if len(p) > 20:
            self._status.target_temp = (
                p[20] / 2.0 if self._status.temp_scale_celsius else float(p[20])
            )

        # ── Heat-Mode-Changed-Event ───────────────────────────────────────────
        if self._status.heat_mode != prev_heat_mode:
            self._heat_mode_changed.set()

        _LOGGER.debug(
            "Status ▶ temp=%.1f°%s target=%.1f heat=%s heating=%s "
            "pumps=[%d,%d,%d] lights=[%s,%s] circ=%s",
            self._status.current_temp or 0,
            "C" if self._status.temp_scale_celsius else "F",
            self._status.target_temp or 0,
            self._status.heat_mode.name,
            self._status.heating,
            self._status.pump1, self._status.pump2, self._status.pump3,
            self._status.light1, self._status.light2,
            self._status.circ_pump,
        )

    def _parse_config(self, p: bytes) -> None:
        if len(p) < 5:
            return
        if len(p) >= 6:
            pi = p[4]
            self._config.pump_speeds[0] = pi & 0x03
            self._config.pump_speeds[1] = (pi >> 2) & 0x03
            self._config.pump_speeds[2] = (pi >> 4) & 0x03
            self._config.pump_speeds[3] = (pi >> 6) & 0x03
            self._config.pump_count = sum(1 for s in self._config.pump_speeds if s > 0)
        if len(p) >= 7:
            mi = p[5]
            self._config.has_circ_pump = bool(mi & 0x02)
            self._config.has_blower    = bool(mi & 0x0C)
            self._config.blower_speeds = (mi >> 2) & 0x03
        if len(p) >= 8:
            li = p[6]
            self._config.light_count = (
                2 if (li & 0x0C) else (1 if (li & 0x03) else 0)
            )
        _LOGGER.info(
            "Config ▶ pumps=%d speeds=%s circ=%s blower=%s lights=%d",
            self._config.pump_count, self._config.pump_speeds,
            self._config.has_circ_pump, self._config.has_blower,
            self._config.light_count,
        )

    def _parse_info(self, p: bytes) -> None:
        if len(p) >= 3:
            self._config.model = f"M{p[0]}_V{p[1]}.{p[2]}"
            _LOGGER.info("Spa-Modell: %s", self._config.model)

    # ── Senden ────────────────────────────────────────────────────────────────

    async def _send(self, data: bytes) -> bool:
        """Rohe Bytes senden."""
        if not self._connected or not self._writer:
            _LOGGER.debug("Senden nicht möglich: nicht verbunden")
            return False
        async with self._send_lock:
            try:
                _LOGGER.debug("TX %d Bytes: %s", len(data), data.hex())
                self._writer.write(data)
                await self._writer.drain()
                return True
            except Exception as exc:
                _LOGGER.error("Sendefehler: %s", exc)
                self._connected = False
                return False

    # ── Steuer-API ────────────────────────────────────────────────────────────

    async def toggle_pump(self, pump_num: int) -> None:
        if 1 <= pump_num <= 6:
            item = ToggleItem.PUMP1 + (pump_num - 1)
            await self._send(_build_frame(MT_TOGGLE, bytes([item, 0x00])))

    async def toggle_light(self, light_num: int) -> None:
        if light_num in (1, 2):
            item = ToggleItem.LIGHT1 if light_num == 1 else ToggleItem.LIGHT2
            await self._send(_build_frame(MT_TOGGLE, bytes([item, 0x00])))

    async def toggle_blower(self) -> None:
        await self._send(_build_frame(MT_TOGGLE, bytes([ToggleItem.BLOWER, 0x00])))

    async def toggle_mister(self) -> None:
        await self._send(_build_frame(MT_TOGGLE, bytes([ToggleItem.MISTER, 0x00])))

    async def toggle_heat_mode(self) -> None:
        await self._send(_build_frame(MT_TOGGLE, bytes([ToggleItem.HEAT_MODE, 0x00])))

    async def toggle_temp_range(self) -> None:
        await self._send(_build_frame(MT_TOGGLE, bytes([ToggleItem.TEMP_RANGE, 0x00])))

    async def set_target_temperature(self, temp: float) -> None:
        """Zieltemperatur setzen."""
        wire = int(temp * 2) if self._status.temp_scale_celsius else int(temp)
        wire = max(0, min(255, wire))
        await self._send(_build_frame(MT_SET_TEMP, bytes([wire])))
        _LOGGER.debug("Zieltemperatur gesetzt: %.1f (wire=%d)", temp, wire)

    async def set_temperature(self, temp: float) -> None:
        """Alias – verwendet von climate.py und number.py."""
        await self.set_target_temperature(temp)

    async def set_heat_mode(self, mode: HeatMode) -> None:
        """
        Heat-Mode setzen durch wiederholtes Toggeln.
        Wartet nach jedem Toggle auf das _heat_mode_changed-Event
        statt blind zu schlafen.
        """
        if self._status.heat_mode == mode:
            return

        for attempt in range(3):
            self._heat_mode_changed.clear()
            await self.toggle_heat_mode()
            try:
                await asyncio.wait_for(self._heat_mode_changed.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                _LOGGER.warning("Heat-Mode-Toggle %d/3: keine Antwort", attempt + 1)
                continue

            if self._status.heat_mode == mode:
                _LOGGER.debug("Heat-Mode erfolgreich gesetzt: %s", mode.name)
                return

        _LOGGER.error(
            "Heat-Mode konnte nicht auf %s gesetzt werden (aktuell: %s)",
            mode.name, self._status.heat_mode.name,
        )

    async def set_time(self, hour: int, minute: int, is_24h: bool = True) -> None:
        flags = 0x80 if is_24h else 0x00
        await self._send(_build_frame(MT_SET_TIME, bytes([flags | hour, minute])))

    async def set_pump(self, pump_num: int, speed: int) -> None:
        if not (1 <= pump_num <= 6):
            return
        current   = getattr(self._status, f"pump{pump_num}", 0)
        max_speed = self._config.pump_speeds[pump_num - 1]
        if max_speed == 0:
            return
        for i in range((speed - current) % (max_speed + 1)):
            await self.toggle_pump(pump_num)
            if i > 0:
                await asyncio.sleep(0.2)

    async def set_light(self, light_num: int, on: bool) -> None:
        current = getattr(self._status, f"light{light_num}", False)
        if current != on:
            await self.toggle_light(light_num)
