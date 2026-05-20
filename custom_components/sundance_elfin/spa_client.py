"""Balboa protocol implementation for Sundance Spa via EW11 RS485-to-TCP bridge."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Callable, Any
from enum import IntEnum
import struct

from .const import (
    MSG_DELIMITER,
    MSG_TYPE_STATUS_UPDATE,
    MSG_TYPE_TOGGLE_ITEM,
    MSG_TYPE_SET_TEMP,
    MSG_TYPE_SETTINGS_REQ,
    MSG_TYPE_CONFIG_RESP,
    MSG_TYPE_INFO_RESP,
    MSG_TYPE_CTS,
    MSG_TYPE_NTS,
    MSG_TYPE_CHANNEL_ASSIGN_REQ,
    MSG_TYPE_CHANNEL_ASSIGN_RESP,
    MSG_TYPE_CHANNEL_ASSIGN_ACK,
    MSG_TYPE_NEW_CLIENT_CTS,
    CHANNEL_BROADCAST,
    CHANNEL_MULTICAST,
    CHANNEL_WIFI,
    ITEM_PUMP_1,
    ITEM_PUMP_2,
    ITEM_PUMP_3,
    ITEM_LIGHT_1,
    ITEM_LIGHT_2,
    ITEM_BLOWER,
    ITEM_TEMP_RANGE,
    ITEM_HEAT_MODE,
    SETTINGS_CONFIG,
    SETTINGS_INFO,
    HEAT_MODE_READY,
    HEAT_MODE_REST,
    HEAT_MODE_READY_IN_REST,
    HEAT_STATE_OFF,
    HEAT_STATE_HEATING,
    HEAT_STATE_HEAT_WAITING,
    TEMP_RANGE_LOW,
    TEMP_RANGE_HIGH,
)

_LOGGER = logging.getLogger(__name__)


class HeatMode(IntEnum):
    """Heat mode enumeration."""
    READY = HEAT_MODE_READY
    REST = HEAT_MODE_REST
    READY_IN_REST = HEAT_MODE_READY_IN_REST


class HeatState(IntEnum):
    """Heat state enumeration."""
    OFF = HEAT_STATE_OFF
    HEATING = HEAT_STATE_HEATING
    HEAT_WAITING = HEAT_STATE_HEAT_WAITING


class TempRange(IntEnum):
    """Temperature range enumeration."""
    LOW = TEMP_RANGE_LOW
    HIGH = TEMP_RANGE_HIGH


class PumpState(IntEnum):
    """Pump state enumeration."""
    OFF = 0
    LOW = 1
    HIGH = 2


@dataclass
class SpaStatus:
    """Current spa status data."""
    # Temperatures
    current_temp: float | None = None
    target_temp: float | None = None
    temp_scale_celsius: bool = True
    temp_range: TempRange = TempRange.HIGH
    
    # Heating
    heat_mode: HeatMode = HeatMode.READY
    heat_state: HeatState = HeatState.OFF
    
    # Pumps (0=off, 1=low, 2=high)
    pump1: PumpState = PumpState.OFF
    pump2: PumpState = PumpState.OFF
    pump3: PumpState = PumpState.OFF
    pump4: PumpState = PumpState.OFF
    pump5: PumpState = PumpState.OFF
    pump6: PumpState = PumpState.OFF
    
    # Other devices
    blower: int = 0  # 0=off, 3=on for some models
    light1: bool = False
    light2: bool = False
    mister: bool = False
    circ_pump: bool = False
    
    # Filter cycles
    filter_mode: int = 0  # 0=off, 1=cycle1, 2=cycle2, 3=both
    
    # Time
    hour: int = 0
    minute: int = 0
    clock_24hr: bool = True
    
    # Status flags
    priming: bool = False
    hold_mode: bool = False
    panel_locked: bool = False
    
    # Model info
    model: str = ""
    software_id: str = ""


@dataclass
class SpaConfig:
    """Spa configuration data."""
    pump_count: int = 2
    pump1_speeds: int = 2  # 1 or 2
    pump2_speeds: int = 2
    pump3_speeds: int = 0
    pump4_speeds: int = 0
    pump5_speeds: int = 0
    pump6_speeds: int = 0
    has_blower: bool = False
    has_mister: bool = False
    has_aux1: bool = False
    has_aux2: bool = False
    has_circ_pump: bool = False
    light_count: int = 1


def calculate_crc8(data: bytes) -> int:
    """Calculate CRC-8 checksum for Balboa protocol.
    
    Polynomial: 0x07
    Initial: 0x02
    Final XOR: 0x02
    """
    crc = 0x02
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ 0x07) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc ^ 0x02


def build_message(channel: int, msg_type: int, data: bytes = b"") -> bytes:
    """Build a Balboa protocol message."""
    # Determine the flag byte based on channel
    flag = 0xAF if channel == CHANNEL_BROADCAST else 0xBF
    
    # Build message content (without delimiters and checksum)
    content = bytes([channel, flag, msg_type]) + data
    length = len(content) + 2  # +2 for length byte and checksum
    
    # Calculate checksum over length + content
    checksum_data = bytes([length]) + content
    crc = calculate_crc8(checksum_data)
    
    # Build full message
    message = bytes([MSG_DELIMITER, length]) + content + bytes([crc, MSG_DELIMITER])
    return message


def parse_message(data: bytes) -> tuple[int, int, bytes] | None:
    """Parse a Balboa protocol message.
    
    Returns: (channel, msg_type, payload) or None if invalid
    """
    if len(data) < 7:  # Minimum message length
        return None
    
    if data[0] != MSG_DELIMITER or data[-1] != MSG_DELIMITER:
        return None
    
    length = data[1]
    if len(data) != length + 2:  # +2 for start delimiter and length byte
        return None
    
    # Verify checksum
    checksum_data = data[1:-2]  # length through payload
    expected_crc = calculate_crc8(checksum_data)
    if data[-2] != expected_crc:
        _LOGGER.debug("CRC mismatch: expected %02X, got %02X", expected_crc, data[-2])
        return None
    
    channel = data[2]
    msg_type = data[4]
    payload = data[5:-2]
    
    return channel, msg_type, payload


class SpaClient:
    """Client for communicating with Balboa spa via EW11 bridge."""
    
    def __init__(self, host: str, port: int = 8899) -> None:
        """Initialize the spa client."""
        self._host = host
        self._port = port
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._connected = False
        self._channel: int | None = None
        self._status = SpaStatus()
        self._config = SpaConfig()
        self._config_loaded = False
        self._update_callbacks: list[Callable[[], None]] = []
        self._receive_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._buffer = bytearray()
        self._last_cts_time: float = 0
        self._config_event = asyncio.Event()
        
    @property
    def host(self) -> str:
        """Return the host."""
        return self._host
    
    @property
    def connected(self) -> bool:
        """Return connection status."""
        return self._connected
    
    @property
    def status(self) -> SpaStatus:
        """Return current status."""
        return self._status
    
    @property
    def config(self) -> SpaConfig:
        """Return spa configuration."""
        return self._config
    
    @property
    def model(self) -> str:
        """Return spa model."""
        return self._status.model
    
    @property
    def temperature(self) -> float | None:
        """Return current temperature."""
        return self._status.current_temp
    
    @property
    def target_temperature(self) -> float | None:
        """Return target temperature."""
        return self._status.target_temp
    
    @property
    def temperature_unit_celsius(self) -> bool:
        """Return True if using Celsius."""
        return self._status.temp_scale_celsius
    
    @property
    def temperature_minimum(self) -> float:
        """Return minimum temperature."""
        if self._status.temp_scale_celsius:
            return 10.0 if self._status.temp_range == TempRange.LOW else 26.5
        return 50.0 if self._status.temp_range == TempRange.LOW else 80.0
    
    @property
    def temperature_maximum(self) -> float:
        """Return maximum temperature."""
        if self._status.temp_scale_celsius:
            return 37.0 if self._status.temp_range == TempRange.LOW else 40.0
        return 99.0 if self._status.temp_range == TempRange.LOW else 104.0
    
    @property
    def heat_mode(self) -> HeatMode:
        """Return current heat mode."""
        return self._status.heat_mode
    
    @property
    def heat_state(self) -> HeatState:
        """Return current heat state."""
        return self._status.heat_state
    
    def add_update_callback(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Add a callback for status updates."""
        self._update_callbacks.append(callback)
        
        def remove_callback():
            if callback in self._update_callbacks:
                self._update_callbacks.remove(callback)
        
        return remove_callback
    
    def _notify_update(self) -> None:
        """Notify all registered callbacks."""
        for callback in self._update_callbacks:
            try:
                callback()
            except Exception as err:
                _LOGGER.error("Error in update callback: %s", err)
    
    async def connect(self) -> bool:
        """Connect to the spa via EW11 bridge."""
        try:
            _LOGGER.info("Connecting to spa at %s:%d", self._host, self._port)
            
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self._host, self._port),
                timeout=10
            )
            
            self._connected = True
            self._buffer.clear()
            
            # Start receive task
            self._receive_task = asyncio.create_task(self._receive_loop())
            
            _LOGGER.info("Connected to spa at %s:%d", self._host, self._port)
            return True
            
        except asyncio.TimeoutError:
            _LOGGER.error("Connection timeout to %s:%d", self._host, self._port)
            return False
        except Exception as err:
            _LOGGER.error("Connection error: %s", err)
            return False
    
    async def disconnect(self) -> None:
        """Disconnect from the spa."""
        self._connected = False
        
        if self._receive_task:
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
        
        _LOGGER.info("Disconnected from spa")
    
    async def _receive_loop(self) -> None:
        """Receive and process messages from the spa."""
        while self._connected and self._reader:
            try:
                data = await asyncio.wait_for(
                    self._reader.read(1024),
                    timeout=30
                )
                
                if not data:
                    _LOGGER.warning("Connection closed by spa")
                    self._connected = False
                    break
                
                self._buffer.extend(data)
                await self._process_buffer()
                
            except asyncio.TimeoutError:
                # Send keepalive or check connection
                _LOGGER.debug("No data received for 30s, connection may be idle")
                continue
            except asyncio.CancelledError:
                break
            except Exception as err:
                _LOGGER.error("Receive error: %s", err)
                self._connected = False
                break
    
    async def _process_buffer(self) -> None:
        """Process buffered data and extract messages."""
        while True:
            # Find message start
            try:
                start = self._buffer.index(MSG_DELIMITER)
                if start > 0:
                    self._buffer = self._buffer[start:]
            except ValueError:
                self._buffer.clear()
                return
            
            if len(self._buffer) < 2:
                return
            
            # Get message length
            msg_len = self._buffer[1] + 2  # +2 for start delimiter and length byte
            
            if len(self._buffer) < msg_len:
                return
            
            # Extract message
            msg_data = bytes(self._buffer[:msg_len])
            self._buffer = self._buffer[msg_len:]
            
            # Parse and handle message
            parsed = parse_message(msg_data)
            if parsed:
                channel, msg_type, payload = parsed
                await self._handle_message(channel, msg_type, payload)
            else:
                _LOGGER.debug("Invalid message: %s", msg_data.hex())
    
    async def _handle_message(self, channel: int, msg_type: int, payload: bytes) -> None:
        """Handle a received message."""
        _LOGGER.debug("Received message: channel=%02X type=%02X payload=%s", 
                     channel, msg_type, payload.hex() if payload else "")
        
        if msg_type == MSG_TYPE_STATUS_UPDATE:
            self._parse_status_update(payload)
            self._notify_update()
        
        elif msg_type == MSG_TYPE_CONFIG_RESP:
            self._parse_config_response(payload)
            self._config_loaded = True
            self._config_event.set()
        
        elif msg_type == MSG_TYPE_INFO_RESP:
            self._parse_info_response(payload)
        
        elif msg_type == MSG_TYPE_NEW_CLIENT_CTS:
            # New client clear to send - we could request a channel
            pass
        
        elif msg_type == MSG_TYPE_CTS:
            # Clear to send for our channel
            self._last_cts_time = asyncio.get_event_loop().time()
        
        elif msg_type == MSG_TYPE_CHANNEL_ASSIGN_RESP:
            if len(payload) >= 3:
                self._channel = payload[0]
                _LOGGER.info("Assigned channel: %02X", self._channel)
    
    def _parse_status_update(self, payload: bytes) -> None:
        """Parse status update message (type 0x13)."""
        if len(payload) < 20:
            _LOGGER.debug("Status update too short: %d bytes", len(payload))
            return
        
        # Byte 0: Spa state (0=running, 1=init, 5=hold, 0x17=test)
        spa_state = payload[0]
        self._status.hold_mode = spa_state == 0x05
        
        # Byte 1: Init mode (0=idle, 1=priming)
        self._status.priming = payload[1] == 0x01
        
        # Byte 2: Current temperature (0xFF if unknown)
        if payload[2] != 0xFF:
            if self._status.temp_scale_celsius:
                self._status.current_temp = payload[2] / 2.0
            else:
                self._status.current_temp = float(payload[2])
        else:
            self._status.current_temp = None
        
        # Bytes 3-4: Time
        self._status.hour = payload[3]
        self._status.minute = payload[4]
        
        # Byte 5: Heating mode
        heat_mode_val = payload[5]
        if heat_mode_val in (0, 1, 3):
            self._status.heat_mode = HeatMode(heat_mode_val)
        
        # Byte 9: Flags (temp scale, clock mode, filter mode, panel locked)
        flags9 = payload[9]
        self._status.temp_scale_celsius = bool(flags9 & 0x01)
        self._status.clock_24hr = bool(flags9 & 0x02)
        self._status.filter_mode = (flags9 >> 3) & 0x03
        self._status.panel_locked = bool(flags9 & 0x20)
        
        # Byte 10: Heating flags
        flags10 = payload[10]
        self._status.temp_range = TempRange.HIGH if (flags10 & 0x04) else TempRange.LOW
        heat_state_val = (flags10 >> 4) & 0x03
        if heat_state_val in (0, 1, 2):
            self._status.heat_state = HeatState(heat_state_val)
        
        # Byte 11: Pumps 1-4
        flags11 = payload[11]
        self._status.pump1 = PumpState(flags11 & 0x03)
        self._status.pump2 = PumpState((flags11 >> 2) & 0x03)
        self._status.pump3 = PumpState((flags11 >> 4) & 0x03)
        self._status.pump4 = PumpState((flags11 >> 6) & 0x03)
        
        # Byte 12: Pumps 5-6
        flags12 = payload[12]
        self._status.pump5 = PumpState(flags12 & 0x03)
        self._status.pump6 = PumpState((flags12 >> 2) & 0x03)
        
        # Byte 13: Circ pump, blower
        flags13 = payload[13]
        self._status.circ_pump = bool(flags13 & 0x02)
        self._status.blower = (flags13 >> 2) & 0x03
        
        # Byte 14: Lights
        flags14 = payload[14]
        self._status.light1 = bool(flags14 & 0x03)
        self._status.light2 = bool((flags14 >> 2) & 0x03)
        
        # Byte 15: Mister
        self._status.mister = bool(payload[15])
        
        # Byte 20: Target temperature
        if len(payload) > 20:
            if self._status.temp_scale_celsius:
                self._status.target_temp = payload[20] / 2.0
            else:
                self._status.target_temp = float(payload[20])
        
        _LOGGER.debug(
            "Status: temp=%.1f target=%.1f heat_mode=%s heat_state=%s pump1=%s pump2=%s light1=%s",
            self._status.current_temp or 0,
            self._status.target_temp or 0,
            self._status.heat_mode.name,
            self._status.heat_state.name,
            self._status.pump1.name,
            self._status.pump2.name,
            self._status.light1
        )
    
    def _parse_config_response(self, payload: bytes) -> None:
        """Parse configuration response message (type 0x2E)."""
        if len(payload) < 6:
            return
        
        # The config response contains pump and feature configuration
        # Byte structure varies by spa model
        
        # Parse pump configuration (simplified)
        pump_config = payload[0] if len(payload) > 0 else 0
        self._config.pump1_speeds = 2 if (pump_config & 0x03) else 0
        self._config.pump2_speeds = 2 if ((pump_config >> 2) & 0x03) else 0
        self._config.pump3_speeds = 2 if ((pump_config >> 4) & 0x03) else 0
        
        # Count pumps
        self._config.pump_count = sum([
            1 if self._config.pump1_speeds else 0,
            1 if self._config.pump2_speeds else 0,
            1 if self._config.pump3_speeds else 0,
        ])
        
        # Features
        if len(payload) > 3:
            features = payload[3]
            self._config.has_blower = bool(features & 0x01)
            self._config.has_circ_pump = bool(features & 0x02)
        
        if len(payload) > 4:
            self._config.light_count = max(1, payload[4] & 0x03)
        
        _LOGGER.debug("Config: %d pumps, blower=%s, lights=%d", 
                     self._config.pump_count, 
                     self._config.has_blower,
                     self._config.light_count)
    
    def _parse_info_response(self, payload: bytes) -> None:
        """Parse information response message (type 0x24)."""
        if len(payload) < 20:
            return
        
        # Bytes 0-3: Software ID (SSID)
        ssid = struct.unpack(">I", payload[0:4])[0]
        model_num = (ssid >> 16) & 0xFFFF
        version = ssid & 0xFFFF
        major = version // 100
        minor = version % 100
        self._status.software_id = f"M{model_num} V{major}.{minor}"
        
        # Bytes 4-11: System model number (ASCII)
        try:
            model_bytes = payload[4:12]
            self._status.model = model_bytes.decode('ascii').strip('\x00').strip()
        except Exception:
            self._status.model = "Unknown"
        
        _LOGGER.info("Spa model: %s, Software: %s", 
                    self._status.model, self._status.software_id)
    
    async def _send_message(self, msg_type: int, data: bytes = b"") -> None:
        """Send a message to the spa."""
        if not self._connected or not self._writer:
            _LOGGER.warning("Cannot send: not connected")
            return
        
        channel = self._channel if self._channel else CHANNEL_WIFI
        message = build_message(channel, msg_type, data)
        
        async with self._lock:
            try:
                self._writer.write(message)
                await self._writer.drain()
                _LOGGER.debug("Sent message type %02X: %s", msg_type, message.hex())
            except Exception as err:
                _LOGGER.error("Send error: %s", err)
                self._connected = False
    
    async def request_configuration(self) -> None:
        """Request spa configuration."""
        # Request configuration (settings code 0x00)
        data = bytes([SETTINGS_CONFIG, 0x00, 0x01])
        await self._send_message(MSG_TYPE_SETTINGS_REQ, data)
    
    async def request_info(self) -> None:
        """Request spa information."""
        # Request info (settings code 0x02)
        data = bytes([SETTINGS_INFO, 0x00, 0x00])
        await self._send_message(MSG_TYPE_SETTINGS_REQ, data)
    
    async def async_configuration_loaded(self) -> None:
        """Wait for configuration to be loaded."""
        # Request config and info
        await self.request_configuration()
        await asyncio.sleep(0.5)
        await self.request_info()
        
        # Wait for config response (with timeout)
        try:
            await asyncio.wait_for(self._config_event.wait(), timeout=10)
        except asyncio.TimeoutError:
            _LOGGER.warning("Timeout waiting for configuration, using defaults")
            self._config_loaded = True
    
    async def set_temperature(self, temperature: float) -> None:
        """Set target temperature."""
        if self._status.temp_scale_celsius:
            temp_byte = int(temperature * 2)
        else:
            temp_byte = int(temperature)
        
        await self._send_message(MSG_TYPE_SET_TEMP, bytes([temp_byte]))
    
    async def toggle_pump(self, pump_num: int) -> None:
        """Toggle a pump."""
        pump_items = {
            1: ITEM_PUMP_1,
            2: ITEM_PUMP_2,
            3: ITEM_PUMP_3,
        }
        if pump_num in pump_items:
            await self._send_message(MSG_TYPE_TOGGLE_ITEM, bytes([pump_items[pump_num], 0x00]))
    
    async def toggle_light(self, light_num: int = 1) -> None:
        """Toggle lights."""
        light_items = {1: ITEM_LIGHT_1, 2: ITEM_LIGHT_2}
        if light_num in light_items:
            await self._send_message(MSG_TYPE_TOGGLE_ITEM, bytes([light_items[light_num], 0x00]))
    
    async def toggle_blower(self) -> None:
        """Toggle blower."""
        await self._send_message(MSG_TYPE_TOGGLE_ITEM, bytes([ITEM_BLOWER, 0x00]))
    
    async def toggle_heat_mode(self) -> None:
        """Toggle heat mode between Ready and Rest."""
        await self._send_message(MSG_TYPE_TOGGLE_ITEM, bytes([ITEM_HEAT_MODE, 0x00]))
    
    async def toggle_temp_range(self) -> None:
        """Toggle temperature range between Low and High."""
        await self._send_message(MSG_TYPE_TOGGLE_ITEM, bytes([ITEM_TEMP_RANGE, 0x00]))
    
    async def set_heat_mode(self, mode: HeatMode) -> None:
        """Set heat mode."""
        # Toggle until we reach desired mode
        current = self._status.heat_mode
        if current != mode:
            await self.toggle_heat_mode()
            # May need multiple toggles for Ready-in-Rest
            await asyncio.sleep(0.5)
            if self._status.heat_mode != mode and mode == HeatMode.READY_IN_REST:
                await self.toggle_heat_mode()
