"""Async TCP client for Sundance/Balboa Spa communication.

Based on the balboa_worldwide_app Ruby implementation:
https://github.com/ccutrer/balboa_worldwide_app

Protocol details:
- Message format: 7E [LENGTH] [SRC] [MSG_TYPE_1] [MSG_TYPE_2] [DATA...] [CRC] 7E
- LENGTH is the count from LENGTH to CRC (inclusive)
- CRC is CRC-8 with init=0x02 and XOR=0x02
- Status message type: 0xAF 0x13
- Toggle message type: 0xBF 0x11
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Callable

_LOGGER = logging.getLogger(__name__)

# Protocol constants
M_STARTEND = 0x7E
DEFAULT_PORT = 8899

# Message type identifiers (2 bytes each)
# Status: sent by spa to all listeners
MSG_TYPE_STATUS = bytes([0xAF, 0x13])
# Toggle item: sent to spa to toggle pumps/lights/etc
MSG_TYPE_TOGGLE = bytes([0xBF, 0x11])
# Set temperature
MSG_TYPE_SET_TEMP = bytes([0xBF, 0x20])
# Set time
MSG_TYPE_SET_TIME = bytes([0xBF, 0x21])
# Acknowledgement/response messages (can be safely ignored)
MSG_TYPE_ACK_06 = bytes([0xBF, 0x06])
MSG_TYPE_ACK_07 = bytes([0xBF, 0x07])

# Message types that are known but don't need processing
KNOWN_IGNORED_MSG_TYPES = {
    bytes([0xBF, 0x06]),  # Acknowledgement type 06
    bytes([0xBF, 0x07]),  # Acknowledgement type 07
}

# Toggle item codes (from balboa_worldwide_app)
TOGGLE_PUMP1 = 0x04
TOGGLE_PUMP2 = 0x05
TOGGLE_PUMP3 = 0x06
TOGGLE_PUMP4 = 0x07
TOGGLE_PUMP5 = 0x08
TOGGLE_PUMP6 = 0x09
TOGGLE_BLOWER = 0x0C
TOGGLE_MISTER = 0x0E
TOGGLE_LIGHT1 = 0x11
TOGGLE_LIGHT2 = 0x12
TOGGLE_AUX1 = 0x16
TOGGLE_AUX2 = 0x17
TOGGLE_HOLD = 0x3C
TOGGLE_TEMP_RANGE = 0x50
TOGGLE_HEAT_MODE = 0x51

# Source addresses
SRC_WIFI_MODULE = 0x0A  # WiFi module address
SRC_BROADCAST = 0xFF    # Broadcast address


@dataclass
class SpaState:
    """Data class holding the current spa state."""
    
    # Connection state
    connected: bool = False
    
    # Temperature (in Celsius)
    current_temp: float | None = None
    target_temp: float | None = None
    temp_scale_celsius: bool = False
    
    # Heating
    is_heating: bool = False
    heat_mode: str = "ready"  # ready, rest, ready_in_rest
    temperature_range: str = "high"  # high, low
    
    # Pumps (0=off, 1=low, 2=high)
    pump1_speed: int = 0
    pump2_speed: int = 0
    pump3_speed: int = 0
    pump4_speed: int = 0
    pump5_speed: int = 0
    pump6_speed: int = 0
    
    # Other accessories
    circ_pump_on: bool = False
    blower: int = 0
    mister: bool = False
    
    # Lights
    light1_on: bool = False
    light2_on: bool = False
    
    # Aux
    aux1_on: bool = False
    aux2_on: bool = False
    
    # Time
    time_hour: int = 0
    time_minute: int = 0
    twenty_four_hour_time: bool = False
    
    # Filter cycles
    filter1_running: bool = False
    filter2_running: bool = False
    
    # Status flags
    priming: bool = False
    hold: bool = False
    
    # Stats
    last_update: float = 0.0
    packets_received: int = 0
    valid_messages: int = 0
    status_updates: int = 0
    last_raw_status: str = ""
    crc_errors: int = 0
    
    @property
    def pump1_on(self) -> bool:
        return self.pump1_speed > 0
    
    @property
    def pump2_on(self) -> bool:
        return self.pump2_speed > 0
    
    @property
    def light_on(self) -> bool:
        return self.light1_on


class SundanceElfinClient:
    """Async TCP client for Sundance Spa via Elfin-EW11A RS485-WiFi adapter.
    
    This client implements the Balboa protocol as documented in balboa_worldwide_app.
    """
    
    def __init__(self, host: str, port: int = DEFAULT_PORT):
        self.host = host
        self.port = port
        self.state = SpaState()
        
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._listen_task: asyncio.Task | None = None
        self._running = False
        self._callbacks: list[Callable[[], None]] = []
        self._lock = asyncio.Lock()
        self._buffer = b""
        
        # Debug mode - logs all packets (set to False for production)
        self._debug = False
        self._log_count = 0
        self._max_logs_per_minute = 30  # Reduced rate limit
        self._log_reset_time = 0.0
        
    def _debug_log(self, msg: str, *args) -> None:
        """Log debug messages with rate limiting.
        
        Uses DEBUG level to avoid flooding Home Assistant logs.
        """
        if not self._debug:
            return
            
        now = time.time()
        if now - self._log_reset_time > 60:
            self._log_count = 0
            self._log_reset_time = now
        
        if self._log_count < self._max_logs_per_minute:
            self._log_count += 1
            _LOGGER.debug("[SUNDANCE] " + msg, *args)
    
    def register_callback(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Register a callback for state changes."""
        self._callbacks.append(callback)
        
        def unregister():
            if callback in self._callbacks:
                self._callbacks.remove(callback)
        return unregister
    
    def _notify(self) -> None:
        """Notify all callbacks."""
        for cb in self._callbacks:
            try:
                cb()
            except Exception:
                _LOGGER.exception("Callback error")
    
    @staticmethod
    def crc8(data: bytes) -> int:
        """Calculate CRC-8 checksum per Balboa spec.
        
        From balboa_worldwide_app/lib/bwa/crc.rb:
        - Uses CRC-8 polynomial 0x07
        - INIT_CRC = 0x02
        - XOR_MASK = 0x02
        """
        crc = 0x02  # Initial value
        for byte in data:
            crc ^= byte
            for _ in range(8):
                if crc & 0x80:
                    crc = ((crc << 1) ^ 0x07) & 0xFF
                else:
                    crc = (crc << 1) & 0xFF
        return crc ^ 0x02  # XOR with mask
    
    async def connect(self) -> bool:
        """Connect to the spa."""
        try:
            self._debug_log("Connecting to %s:%s...", self.host, self.port)
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port),
                timeout=10.0
            )
            self.state.connected = True
            self._buffer = b""
            _LOGGER.info("Connected to Sundance Spa at %s:%s", self.host, self.port)
            self._notify()
            return True
        except Exception as e:
            _LOGGER.error("Connection failed: %s", e)
            self.state.connected = False
            return False
    
    async def disconnect(self) -> None:
        """Disconnect from the spa."""
        self._running = False
        if self._listen_task:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
        self._reader = None
        self._writer = None
        self.state.connected = False
        _LOGGER.info("Disconnected")
        self._notify()
    
    async def start(self) -> None:
        """Start the client."""
        self._running = True
        if await self.connect():
            self._listen_task = asyncio.create_task(self._listen_loop())
    
    async def stop(self) -> None:
        """Stop the client."""
        await self.disconnect()
    
    async def _listen_loop(self) -> None:
        """Main receive loop."""
        reconnect_delay = 5
        
        while self._running:
            try:
                if not self._reader or not self.state.connected:
                    self._debug_log("Not connected, waiting %ds before reconnect...", reconnect_delay)
                    await asyncio.sleep(reconnect_delay)
                    reconnect_delay = min(reconnect_delay * 2, 60)
                    if self._running:
                        if await self.connect():
                            reconnect_delay = 5
                    continue
                
                # Read available data
                try:
                    chunk = await asyncio.wait_for(
                        self._reader.read(1024),
                        timeout=30.0
                    )
                except asyncio.TimeoutError:
                    self._debug_log("Read timeout - connection may be idle")
                    continue
                
                if not chunk:
                    self._debug_log("Connection closed by remote")
                    self.state.connected = False
                    self._notify()
                    continue
                
                self._buffer += chunk
                self.state.packets_received += 1
                
                # Process all complete messages in buffer
                self._process_buffer()
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                _LOGGER.error("Listen error: %s", e)
                self.state.connected = False
                self._notify()
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, 60)
                if self._running:
                    await self.connect()
    
    def _process_buffer(self) -> None:
        """Process complete messages from the buffer.
        
        Message format (from balboa_worldwide_app):
        [0x7E] [LENGTH] [SRC] [TYPE1] [TYPE2] [DATA...] [CRC] [0x7E]
        
        LENGTH = number of bytes from LENGTH to CRC (inclusive)
        So total message length = LENGTH + 2 (for start and end bytes)
        """
        while len(self._buffer) >= 5:  # Minimum message size
            # Find start byte
            start_idx = self._buffer.find(bytes([M_STARTEND]))
            if start_idx == -1:
                self._buffer = b""
                return
            
            # Discard bytes before start
            if start_idx > 0:
                self._debug_log("Discarding %d bytes before start: %s", 
                              start_idx, self._buffer[:start_idx].hex())
                self._buffer = self._buffer[start_idx:]
            
            if len(self._buffer) < 2:
                return
            
            # Get length byte
            length = self._buffer[1]
            
            # Sanity check length (valid range is 5-126 per balboa_worldwide_app)
            if length < 5 or length >= M_STARTEND:
                self._debug_log("Invalid length %d, skipping byte", length)
                self._buffer = self._buffer[1:]
                continue
            
            # Check if we have the full message
            total_len = length + 2  # +2 for start byte and end byte
            if len(self._buffer) < total_len:
                return  # Wait for more data
            
            # Check end byte
            if self._buffer[total_len - 1] != M_STARTEND:
                self._debug_log("Missing end byte at position %d, got 0x%02x", 
                              total_len - 1, self._buffer[total_len - 1])
                self._buffer = self._buffer[1:]
                continue
            
            # Extract message
            message = self._buffer[:total_len]
            
            # Verify CRC (calculated over bytes from LENGTH to before CRC)
            crc_data = message[1:total_len - 2]  # LENGTH through DATA
            calculated_crc = self.crc8(crc_data)
            received_crc = message[total_len - 2]
            
            if calculated_crc != received_crc:
                self.state.crc_errors += 1
                self._debug_log("CRC ERROR #%d: calc=0x%02x recv=0x%02x msg=%s", 
                              self.state.crc_errors, calculated_crc, received_crc, message.hex())
                self._buffer = self._buffer[1:]
                continue
            
            # Valid message!
            self.state.valid_messages += 1
            self._debug_log("VALID MSG #%d: %s", self.state.valid_messages, message.hex())
            
            # Remove from buffer and process
            self._buffer = self._buffer[total_len:]
            self._process_message(message)
    
    def _process_message(self, msg: bytes) -> None:
        """Process a validated message.
        
        Format: 7E [LEN] [SRC] [TYPE1] [TYPE2] [DATA...] [CRC] 7E
        """
        if len(msg) < 7:  # Minimum: 7E LEN SRC T1 T2 CRC 7E
            return
        
        src = msg[2]
        msg_type = msg[3:5]  # Two-byte message type
        data = msg[5:-2]     # Data between type and CRC
        
        self._debug_log("MSG: src=0x%02x type=%s datalen=%d", src, msg_type.hex(), len(data))
        
        # Status message (0xAF 0x13)
        if msg_type == MSG_TYPE_STATUS:
            self._parse_status(data)
        elif msg_type in KNOWN_IGNORED_MSG_TYPES:
            # Known acknowledgement messages - silently ignore
            pass
        else:
            _LOGGER.debug("[SUNDANCE] Unknown msg type: %s (full: %s)", msg_type.hex(), msg.hex())
    
    def _parse_status(self, data: bytes) -> None:
        """Parse status message data.
        
        Based on balboa_worldwide_app/lib/bwa/messages/status.rb:
        data[0] = flags (hold)
        data[1] = priming flag (0x01 = priming)
        data[2] = current temperature
        data[3] = hour
        data[4] = minute
        data[5] = heating mode flags
        data[6] = notification
        data[7-8] = ?
        data[9] = temp scale (bit 0), 24h time (bit 1), filter cycles (bits 2-3)
        data[10] = heating (bits 4-5), temp range (bit 2)
        data[11] = pumps 1-4 state
        data[12] = pumps 5-6 state
        data[13] = circ pump (bit 1), blower (bits 2-3)
        data[14] = lights 1-2
        data[15] = mister (bit 0), aux1 (bit 3), aux2 (bit 4)
        ...
        data[20] = target temperature
        """
        if len(data) < 21:
            _LOGGER.debug("[SUNDANCE] Status too short: %d bytes (need 21+)", len(data))
            return
        
        # Store raw hex for diagnostic sensor
        self.state.last_raw_status = data.hex()
        
        try:
            # Hold mode
            self.state.hold = (data[0] & 0x05) != 0
            
            # Priming
            self.state.priming = data[1] == 0x01
            
            # Current temperature
            raw_temp = data[2]
            
            # Temperature scale from data[9]
            self.state.temp_scale_celsius = (data[9] & 0x01) == 0x01
            self.state.twenty_four_hour_time = (data[9] & 0x02) == 0x02
            self.state.filter1_running = (data[9] & 0x04) != 0
            self.state.filter2_running = (data[9] & 0x08) != 0
            
            if raw_temp != 0xFF:
                if self.state.temp_scale_celsius:
                    # In Celsius mode, temp is stored as C * 2
                    self.state.current_temp = raw_temp / 2.0
                else:
                    # In Fahrenheit mode, convert to Celsius
                    self.state.current_temp = round((raw_temp - 32) * 5 / 9, 1)
            else:
                self.state.current_temp = None
            
            # Time
            self.state.time_hour = data[3]
            self.state.time_minute = data[4]
            
            # Heating mode from data[5]
            heat_mode_val = data[5] & 0x03
            self.state.heat_mode = {0: "ready", 1: "rest", 2: "ready_in_rest"}.get(heat_mode_val, "ready")
            
            # Heating state and temp range from data[10]
            self.state.is_heating = (data[10] & 0x30) != 0
            self.state.temperature_range = "high" if (data[10] & 0x04) else "low"
            
            # Pump states from data[11] - 2 bits each for pumps 1-4
            pumps = data[11]
            self.state.pump1_speed = pumps & 0x03
            self.state.pump2_speed = (pumps >> 2) & 0x03
            self.state.pump3_speed = (pumps >> 4) & 0x03
            self.state.pump4_speed = (pumps >> 6) & 0x03
            
            # Pumps 5-6 from data[12]
            if len(data) > 12:
                pumps56 = data[12]
                self.state.pump5_speed = pumps56 & 0x03
                self.state.pump6_speed = (pumps56 >> 2) & 0x03
            
            # Circ pump and blower from data[13]
            if len(data) > 13:
                self.state.circ_pump_on = (data[13] & 0x02) == 0x02
                self.state.blower = (data[13] >> 2) & 0x03
            
            # Lights from data[14]
            if len(data) > 14:
                self.state.light1_on = (data[14] & 0x03) != 0
                self.state.light2_on = ((data[14] >> 2) & 0x03) != 0
            
            # Mister and aux from data[15]
            if len(data) > 15:
                self.state.mister = (data[15] & 0x01) == 0x01
                self.state.aux1_on = (data[15] & 0x08) != 0
                self.state.aux2_on = (data[15] & 0x10) != 0
            
            # Target temperature from data[20]
            if len(data) > 20:
                raw_target = data[20]
                if self.state.temp_scale_celsius:
                    self.state.target_temp = raw_target / 2.0
                else:
                    self.state.target_temp = round((raw_target - 32) * 5 / 9, 1)
            
            self.state.status_updates += 1
            self.state.last_update = time.time()
            
            self._notify()
            
        except Exception as e:
            _LOGGER.exception("Status parse error: %s", e)
    
    def _build_message(self, msg_type: bytes, data: bytes = b"") -> bytes:
        """Build a complete message to send to the spa.
        
        Format: 7E [LEN] [SRC] [TYPE1] [TYPE2] [DATA...] [CRC] 7E
        """
        # Length = LEN + SRC + TYPE1 + TYPE2 + DATA + CRC = 4 + len(data) + 1 = 5 + len(data)
        length = 5 + len(data)
        
        # Build message body (LENGTH through DATA, for CRC calculation)
        body = bytes([length, SRC_WIFI_MODULE]) + msg_type + data
        
        # Calculate CRC over body
        crc = self.crc8(body)
        
        # Complete message
        message = bytes([M_STARTEND]) + body + bytes([crc, M_STARTEND])
        
        return message
    
    async def send_message(self, msg_type: bytes, data: bytes = b"") -> bool:
        """Send a message to the spa."""
        async with self._lock:
            if not self._writer or not self.state.connected:
                self._debug_log("SEND FAILED: not connected")
                return False
            
            message = self._build_message(msg_type, data)
            self._debug_log("SENDING: %s", message.hex())
            
            try:
                self._writer.write(message)
                await self._writer.drain()
                self._debug_log("SEND OK")
                return True
            except Exception as e:
                _LOGGER.error("Send failed: %s", e)
                self.state.connected = False
                self._notify()
                return False
    
    async def toggle_item(self, item: int) -> bool:
        """Toggle a spa item (pump, light, etc).
        
        Message format: 7E [LEN] [SRC] BF 11 [ITEM] 00 [CRC] 7E
        """
        self._debug_log("TOGGLE: item=0x%02x", item)
        return await self.send_message(MSG_TYPE_TOGGLE, bytes([item, 0x00]))
    
    async def toggle_pump1(self) -> bool:
        """Toggle pump 1."""
        self._debug_log("Toggle pump1 (current speed=%d)", self.state.pump1_speed)
        result = await self.toggle_item(TOGGLE_PUMP1)
        if result:
            # Optimistic update: cycle through 0 -> 1 -> 2 -> 0
            self.state.pump1_speed = (self.state.pump1_speed + 1) % 3
            self._notify()
        return result
    
    async def toggle_pump2(self) -> bool:
        """Toggle pump 2."""
        self._debug_log("Toggle pump2 (current speed=%d)", self.state.pump2_speed)
        result = await self.toggle_item(TOGGLE_PUMP2)
        if result:
            self.state.pump2_speed = (self.state.pump2_speed + 1) % 3
            self._notify()
        return result
    
    async def toggle_light(self) -> bool:
        """Toggle light 1."""
        self._debug_log("Toggle light1 (current=%s)", self.state.light1_on)
        result = await self.toggle_item(TOGGLE_LIGHT1)
        if result:
            self.state.light1_on = not self.state.light1_on
            self._notify()
        return result
    
    async def set_temperature(self, temp_c: float) -> bool:
        """Set the target temperature in Celsius.
        
        Message format: 7E [LEN] [SRC] BF 20 [TEMP] [CRC] 7E
        """
        if self.state.temp_scale_celsius:
            raw_temp = int(temp_c * 2)
        else:
            # Convert to Fahrenheit
            temp_f = (temp_c * 9 / 5) + 32
            raw_temp = int(temp_f)
        
        self._debug_log("Set temp: %.1f C -> raw=%d", temp_c, raw_temp)
        result = await self.send_message(MSG_TYPE_SET_TEMP, bytes([raw_temp]))
        if result:
            self.state.target_temp = temp_c
            self._notify()
        return result
    
    async def increase_temp(self) -> bool:
        """Increase target temperature by 0.5C."""
        if self.state.target_temp is not None:
            return await self.set_temperature(self.state.target_temp + 0.5)
        return False
    
    async def decrease_temp(self) -> bool:
        """Decrease target temperature by 0.5C."""
        if self.state.target_temp is not None:
            return await self.set_temperature(self.state.target_temp - 0.5)
        return False
