"""Async TCP client for Sundance Spa communication.

Based on the Jacuzzi-RS485 protocol implementation:
https://github.com/jackbrown1993/Jacuzzi-RS485

Sundance Spas use a modified Balboa/Jacuzzi protocol with message type 0x16
for status updates instead of Balboa's 0x13.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Callable

_LOGGER = logging.getLogger(__name__)

# Protocol constants
M_STARTEND = 0x7E
DEFAULT_PORT = 8899

# Message type identifiers
# Jacuzzi uses 0x16 for status updates, Balboa uses 0x13
MSG_TYPE_STATUS_JACUZZI = 0x16
MSG_TYPE_STATUS_BALBOA = 0x13

# Button codes for commands (msg type 0x17)
C_PUMP1 = 0x04
C_PUMP2 = 0x05
C_PUMP3 = 0x06
C_LIGHT1 = 0x11
C_LIGHT2 = 0x12
C_TEMP_UP = 0x01
C_TEMP_DOWN = 0x02
C_BLOWER = 0x0C

# Temperature scale
TSCALE_F = 0
TSCALE_C = 1


@dataclass
class SpaState:
    """Data class holding the current spa state."""
    
    # Connection state
    connected: bool = False
    
    # Temperature
    current_temp: float | None = None
    target_temp: float | None = None
    temp_scale: int = TSCALE_C  # 0=F, 1=C
    
    # Heating
    is_heating: bool = False
    heat_mode: int = 0  # 0=Ready, 1=Rest, 2=Ready in Rest
    
    # Pumps (0=off, 1=low, 2=high)
    pump1_speed: int = 0
    pump2_speed: int = 0
    pump3_speed: int = 0
    circ_pump_on: bool = False
    
    # Light
    light_on: bool = False
    light_brightness: int = 0
    
    # Time
    time_hour: int = 0
    time_minute: int = 0
    
    # Date (Jacuzzi specific)
    day_of_month: int = 0
    current_month: int = 0
    current_year: int = 0
    
    # Filter
    filter_mode: int = 0
    
    # Error
    error_code: int = 0
    
    # Stats
    last_update: float = 0.0
    packets_received: int = 0
    status_updates: int = 0
    last_raw_status: str = ""
    
    @property
    def pump1_on(self) -> bool:
        return self.pump1_speed > 0
    
    @property
    def pump2_on(self) -> bool:
        return self.pump2_speed > 0


class SundanceElfinClient:
    """Async TCP client for Sundance Spa via Elfin-EW11A RS485-WiFi adapter.
    
    This client implements the Jacuzzi/Sundance variant of the Balboa protocol.
    Key differences from standard Balboa:
    - Status update message type is 0x16 instead of 0x13
    - Byte positions for temperature and status flags differ
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
        self._prior_status: bytes | None = None
        
        # Rate limiting for logs
        self._last_log: dict[str, float] = {}
        
    def _log_rate_limited(self, level: int, key: str, msg: str, *args) -> None:
        """Log with rate limiting to prevent spam."""
        now = time.time()
        if key in self._last_log and now - self._last_log[key] < 10.0:
            return
        self._last_log[key] = now
        _LOGGER.log(level, msg, *args)
        
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
    
    async def connect(self) -> bool:
        """Connect to the spa."""
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port),
                timeout=10.0
            )
            self.state.connected = True
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
    
    def balboa_calc_cs(self, data: bytes, length: int) -> int:
        """Calculate Balboa CRC-8 checksum.
        
        CRC-8 with:
        - Poly = 0x07
        - Init = 0x02
        - XorOut = 0x02
        """
        crc = 0xB5
        for i in range(length):
            for j in range(8):
                bit = crc & 0x80
                crc = ((crc << 1) & 0xFF) | ((data[i] >> (7 - j)) & 0x01)
                if bit:
                    crc = crc ^ 0x07
            crc &= 0xFF
        for j in range(8):
            bit = crc & 0x80
            crc = (crc << 1) & 0xFF
            if bit:
                crc ^= 0x07
        return crc ^ 0x02
    
    async def _listen_loop(self) -> None:
        """Main receive loop."""
        while self._running:
            try:
                if not self._reader or not self.state.connected:
                    await asyncio.sleep(30)
                    if self._running:
                        await self.connect()
                    continue
                
                msg = await self._read_one_message()
                if msg:
                    self._process_message(msg)
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._log_rate_limited(logging.ERROR, "listen_err", "Listen error: %s", e)
                self.state.connected = False
                self._notify()
                await asyncio.sleep(30)
                if self._running:
                    await self.connect()
    
    async def _read_one_message(self) -> bytes | None:
        """Read one complete message from the spa.
        
        Message format:
        [0x7E] [LENGTH] [DATA...] [CHECKSUM] [0x7E]
        
        LENGTH includes everything from LENGTH to CHECKSUM (inclusive).
        """
        if not self._reader:
            return None
        
        try:
            # Read header (start byte + length)
            header = await asyncio.wait_for(
                self._reader.readexactly(2),
                timeout=60.0
            )
        except asyncio.TimeoutError:
            return None
        except Exception as e:
            self._log_rate_limited(logging.ERROR, "read_header", "Read header error: %s", e)
            self.state.connected = False
            self._notify()
            return None
        
        # Check for start byte
        if header[0] == M_STARTEND:
            rlen = header[1]
        elif header[1] == M_STARTEND:
            # Misaligned, try to recover
            try:
                rlen_bytes = await self._reader.readexactly(1)
                rlen = rlen_bytes[0]
            except Exception:
                return None
        else:
            return None
        
        # Sanity check length
        if rlen < 3 or rlen > 128:
            return None
        
        # Read the rest of the message
        try:
            data = await asyncio.wait_for(
                self._reader.readexactly(rlen),
                timeout=5.0
            )
        except Exception as e:
            self._log_rate_limited(logging.ERROR, "read_data", "Read data error: %s", e)
            return None
        
        full_data = header + data
        self.state.packets_received += 1
        
        # Verify checksum (checksum is second-to-last byte, last byte is 0x7E)
        if full_data[-1] != M_STARTEND:
            self._log_rate_limited(logging.DEBUG, "no_end", "Message missing end byte")
            return None
        
        # Calculate and verify CRC
        crc = self.balboa_calc_cs(full_data[1:], rlen - 1)
        if crc != full_data[-2]:
            self._log_rate_limited(logging.DEBUG, "bad_crc", 
                "Bad CRC: calc=%02x, got=%02x", crc, full_data[-2])
            return None
        
        return full_data
    
    def _process_message(self, data: bytes) -> None:
        """Process a received message."""
        if len(data) < 5:
            return
        
        # Message format: 7E [LEN] [ADDR1] [ADDR2] [MSG_TYPE] [DATA...] [CRC] 7E
        # Addresses: 0xFF 0xAF = broadcast from panel/controller
        addr1 = data[2]
        addr2 = data[3]
        msg_type = data[4]
        
        # Status update (Jacuzzi uses 0x16, Balboa uses 0x13)
        if addr1 == 0xFF and addr2 == 0xAF:
            if msg_type == MSG_TYPE_STATUS_JACUZZI:
                self._parse_jacuzzi_status(data)
            elif msg_type == MSG_TYPE_STATUS_BALBOA:
                self._parse_balboa_status(data)
            else:
                self._log_rate_limited(logging.DEBUG, f"unk_msg_{msg_type}",
                    "Unknown broadcast msg type 0x%02x: %s", msg_type, data.hex()[:60])
    
    def _parse_jacuzzi_status(self, data: bytes) -> None:
        """Parse Jacuzzi status update (msg type 0x16).
        
        Byte positions based on Jacuzzi-RS485 project:
        data[5] = time hour
        data[6] = time minute
        data[7] = day of week (bits 7-5) + day of month (bits 4-0)
        data[8] = current month
        data[9] = current year (since 2000)
        data[10] = filter2 mode (bits 7-6), heat mode (bits 5-4), spa state (bits 3-0)
        data[11] = error code
        data[12] = current temp (raw)
        data[13] = don't care
        data[14] = target/set temp (raw)
        data[15] = pump states: pump3(7-6), pump2(5-4), pump1(3-2), ?(1-0)
        data[16] = circ pump / blower state
        data[17] = light state
        """
        if len(data) < 20:
            return
        
        # Check if status changed (skip redundant updates)
        status_hex = data.hex()
        if self._prior_status and status_hex == self._prior_status.hex():
            return
        self._prior_status = data
        
        # Store raw for debugging
        self.state.last_raw_status = status_hex
        
        try:
            # Time
            self.state.time_hour = data[5]
            self.state.time_minute = data[6]
            
            # Date
            self.state.day_of_month = data[7] & 0x1F
            self.state.current_month = data[8]
            self.state.current_year = data[9] + 2000
            
            # Heat mode and state
            self.state.heat_mode = (data[10] >> 4) & 0x03
            self.state.is_heating = self.state.heat_mode > 0
            
            # Error code
            self.state.error_code = data[11]
            
            # Current temperature (raw value)
            raw_temp = data[12]
            if raw_temp != 0xFF:  # 0xFF = unknown/unavailable
                # Temperature is stored as Fahrenheit / 2 when in Celsius mode
                # Or direct Fahrenheit when in F mode
                # Sundance typically uses Celsius, so value is F*2
                # Convert: raw/2 = F, then (F-32)*5/9 = C
                temp_f = raw_temp / 2.0 if self.state.temp_scale == TSCALE_C else raw_temp
                self.state.current_temp = round((temp_f - 32) * 5 / 9, 1)
            else:
                self.state.current_temp = None
            
            # Target temperature
            raw_settemp = data[14]
            if raw_settemp != 0xFF:
                temp_f = raw_settemp / 2.0 if self.state.temp_scale == TSCALE_C else raw_settemp
                self.state.target_temp = round((temp_f - 32) * 5 / 9, 1)
            
            # Pump states from byte 15
            # Bits 7-6 = Pump 3
            # Bits 5-4 = Pump 2
            # Bits 3-2 = Pump 1
            pump_byte = data[15]
            self.state.pump3_speed = (pump_byte >> 6) & 0x03
            self.state.pump2_speed = (pump_byte >> 4) & 0x03
            self.state.pump1_speed = (pump_byte >> 2) & 0x03
            
            # Circ pump from byte 16
            if len(data) > 16:
                self.state.circ_pump_on = (data[16] & 0x02) != 0
            
            # Light from byte 17
            if len(data) > 17:
                self.state.light_on = (data[17] & 0x03) != 0
            
            self.state.status_updates += 1
            self.state.last_update = time.time()
            
            self._log_rate_limited(logging.DEBUG, "status",
                "Status: temp=%.1f/%.1f°C pump1=%d pump2=%d light=%s heat=%s",
                self.state.current_temp or 0,
                self.state.target_temp or 0,
                self.state.pump1_speed,
                self.state.pump2_speed,
                self.state.light_on,
                self.state.is_heating
            )
            
            self._notify()
            
        except Exception as e:
            self._log_rate_limited(logging.WARNING, "parse_err",
                "Parse error: %s - %s", e, data.hex()[:40])
    
    def _parse_balboa_status(self, data: bytes) -> None:
        """Parse Balboa status update (msg type 0x13).
        
        Byte positions for standard Balboa:
        data[7] = current temp
        data[8] = time hour
        data[9] = time minute
        data[10] = heat mode
        data[14] = temp scale (bit 0), time scale (bit 1)
        data[15] = heat state (bits 5-4), temp range (bit 2)
        data[16] = pump 1-4 states
        data[19] = light states
        data[25] = target temp
        """
        if len(data) < 28:
            return
        
        status_hex = data.hex()
        if self._prior_status and status_hex == self._prior_status.hex():
            return
        self._prior_status = data
        
        self.state.last_raw_status = status_hex
        
        try:
            # Temperature scale from flags
            self.state.temp_scale = TSCALE_C if (data[14] & 0x01) else TSCALE_F
            
            # Time
            self.state.time_hour = data[8]
            self.state.time_minute = data[9]
            
            # Current temperature
            raw_temp = data[7]
            if raw_temp != 0xFF:
                if self.state.temp_scale == TSCALE_C:
                    self.state.current_temp = raw_temp / 2.0
                else:
                    self.state.current_temp = round((raw_temp - 32) * 5 / 9, 1)
            
            # Target temperature
            raw_settemp = data[25]
            if self.state.temp_scale == TSCALE_C:
                self.state.target_temp = raw_settemp / 2.0
            else:
                self.state.target_temp = round((raw_settemp - 32) * 5 / 9, 1)
            
            # Heat mode and state
            self.state.heat_mode = data[10] & 0x03
            self.state.is_heating = ((data[15] >> 4) & 0x03) > 0
            
            # Pump states from byte 16
            pump_byte = data[16]
            self.state.pump1_speed = (pump_byte >> 0) & 0x03
            self.state.pump2_speed = (pump_byte >> 2) & 0x03
            
            # Light from byte 19
            self.state.light_on = ((data[19] >> 0) & 0x03) > 0
            
            self.state.status_updates += 1
            self.state.last_update = time.time()
            
            self._notify()
            
        except Exception as e:
            self._log_rate_limited(logging.WARNING, "parse_balboa_err",
                "Balboa parse error: %s", e)
    
    async def send_message(self, *msg_bytes: int) -> bool:
        """Send a message to the spa."""
        async with self._lock:
            if not self._writer or not self.state.connected:
                _LOGGER.error("Cannot send: not connected")
                return False
            
            # Build message: 7E [LEN] [DATA...] [CRC] 7E
            message_length = len(msg_bytes) + 2  # +2 for CRC and end byte
            msg = bytearray(message_length + 2)
            msg[0] = M_STARTEND
            msg[1] = message_length
            msg[2:2 + len(msg_bytes)] = msg_bytes
            msg[-2] = self.balboa_calc_cs(msg[1:message_length], message_length - 1)
            msg[-1] = M_STARTEND
            
            try:
                _LOGGER.debug("Sending: %s", msg.hex())
                self._writer.write(msg)
                await self._writer.drain()
                return True
            except Exception as e:
                _LOGGER.error("Send failed: %s", e)
                self.state.connected = False
                self._notify()
                return False
    
    async def send_button(self, button: int) -> bool:
        """Send a button press command.
        
        Command format for Jacuzzi:
        [CHANNEL] 0xBF 0x17 [BUTTON]
        
        For WiFi module, channel is typically 0x0A.
        """
        # 0x0A = WiFi module channel, 0xBF = always, 0x17 = button press msg type
        return await self.send_message(0x0A, 0xBF, 0x17, button)
    
    async def toggle_pump1(self) -> bool:
        """Toggle pump 1."""
        _LOGGER.info("Toggling pump 1")
        result = await self.send_button(C_PUMP1)
        if result:
            # Optimistic update
            self.state.pump1_speed = (self.state.pump1_speed + 1) % 3
            self._notify()
        return result
    
    async def toggle_pump2(self) -> bool:
        """Toggle pump 2."""
        _LOGGER.info("Toggling pump 2")
        result = await self.send_button(C_PUMP2)
        if result:
            self.state.pump2_speed = (self.state.pump2_speed + 1) % 3
            self._notify()
        return result
    
    async def toggle_light(self) -> bool:
        """Toggle light."""
        _LOGGER.info("Toggling light")
        result = await self.send_button(C_LIGHT1)
        if result:
            self.state.light_on = not self.state.light_on
            self._notify()
        return result
    
    async def increase_temp(self) -> bool:
        """Increase target temperature."""
        _LOGGER.info("Increasing temperature")
        return await self.send_button(C_TEMP_UP)
    
    async def decrease_temp(self) -> bool:
        """Decrease target temperature."""
        _LOGGER.info("Decreasing temperature")
        return await self.send_button(C_TEMP_DOWN)
    
    async def set_target_temperature(self, temp_c: float) -> bool:
        """Set target temperature by sending temp up/down buttons."""
        if self.state.target_temp is None:
            _LOGGER.warning("Cannot set temp: current target unknown")
            return False
        
        diff = temp_c - self.state.target_temp
        steps = int(abs(diff) / 0.5)  # 0.5°C per button press
        
        _LOGGER.info("Setting temp from %.1f to %.1f°C (%d steps)",
            self.state.target_temp, temp_c, steps)
        
        for _ in range(min(steps, 20)):
            if diff > 0:
                await self.increase_temp()
            else:
                await self.decrease_temp()
            await asyncio.sleep(0.5)
        
        return True
