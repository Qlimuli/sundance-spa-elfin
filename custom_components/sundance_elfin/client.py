"""Async TCP client for Sundance Spa Elfin communication."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Callable

from .const import (
    CONNECTION_TIMEOUT,
    MSG_SET_TEMP,
    MSG_TOGGLE_LIGHT,
    MSG_TOGGLE_PUMP1,
    MSG_TOGGLE_PUMP2,
    PACKET_END,
    PACKET_START,
    POS_CURRENT_TEMP,
    POS_HEATING_STATE,
    POS_LIGHT_STATE,
    POS_PUMP1_STATE,
    POS_PUMP2_STATE,
    POS_TARGET_TEMP,
    RECONNECT_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class SpaState:
    """Data class holding the current spa state."""

    current_temp: float = 0.0
    target_temp: float = 37.0
    is_heating: bool = False
    pump1_on: bool = False
    pump2_on: bool = False
    light_on: bool = False
    connected: bool = False
    last_update: float = 0.0


@dataclass
class SundanceElfinClient:
    """Async TCP client for Elfin-EW11A RS485 adapter.
    
    This client maintains a persistent TCP connection to the Elfin adapter,
    reads incoming RS485 data packets, and sends control commands.
    
    The protocol parsing methods are designed as placeholders that you can
    customize with the actual hex codes from your Sundance/Jacuzzi spa.
    """

    host: str
    port: int
    state: SpaState = field(default_factory=SpaState)
    
    _reader: asyncio.StreamReader | None = field(default=None, repr=False)
    _writer: asyncio.StreamWriter | None = field(default=None, repr=False)
    _listen_task: asyncio.Task | None = field(default=None, repr=False)
    _running: bool = field(default=False, repr=False)
    _callbacks: list[Callable[[], None]] = field(default_factory=list, repr=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    def register_callback(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Register a callback to be called when state changes.
        
        Returns a function to unregister the callback.
        """
        self._callbacks.append(callback)
        
        def unregister() -> None:
            if callback in self._callbacks:
                self._callbacks.remove(callback)
        
        return unregister

    def _notify_callbacks(self) -> None:
        """Notify all registered callbacks of state change."""
        for callback in self._callbacks:
            try:
                callback()
            except Exception:
                _LOGGER.exception("Error in state callback")

    async def connect(self) -> bool:
        """Establish TCP connection to the Elfin adapter."""
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port),
                timeout=CONNECTION_TIMEOUT,
            )
            self.state.connected = True
            _LOGGER.info("Connected to Sundance Spa at %s:%s", self.host, self.port)
            self._notify_callbacks()
            return True
        except asyncio.TimeoutError:
            _LOGGER.error("Timeout connecting to %s:%s", self.host, self.port)
            self.state.connected = False
            return False
        except OSError as err:
            _LOGGER.error("Failed to connect to %s:%s: %s", self.host, self.port, err)
            self.state.connected = False
            return False

    async def disconnect(self) -> None:
        """Close the TCP connection."""
        self._running = False
        
        if self._listen_task and not self._listen_task.done():
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
        _LOGGER.info("Disconnected from Sundance Spa")
        self._notify_callbacks()

    async def start(self) -> None:
        """Start the client and begin listening for data."""
        self._running = True
        if await self.connect():
            self._listen_task = asyncio.create_task(self._listen_loop())

    async def stop(self) -> None:
        """Stop the client and close connection."""
        await self.disconnect()

    async def _listen_loop(self) -> None:
        """Main loop that reads data from the TCP stream."""
        buffer = bytearray()
        
        while self._running:
            try:
                if not self._reader or not self.state.connected:
                    await self._reconnect()
                    continue
                
                # Read data from the stream
                try:
                    data = await asyncio.wait_for(
                        self._reader.read(256),
                        timeout=30.0,
                    )
                except asyncio.TimeoutError:
                    # No data received, but connection might still be alive
                    continue
                
                if not data:
                    # Connection closed by remote
                    _LOGGER.warning("Connection closed by Elfin adapter")
                    self.state.connected = False
                    self._notify_callbacks()
                    await self._reconnect()
                    continue
                
                # Add received data to buffer
                buffer.extend(data)
                _LOGGER.debug("Received %d bytes: %s", len(data), data.hex())
                
                # Process complete packets in buffer
                buffer = await self._process_buffer(buffer)
                
            except asyncio.CancelledError:
                break
            except Exception as err:
                _LOGGER.exception("Error in listen loop: %s", err)
                self.state.connected = False
                self._notify_callbacks()
                await self._reconnect()

    async def _reconnect(self) -> None:
        """Handle reconnection with backoff."""
        if not self._running:
            return
        
        _LOGGER.info("Attempting to reconnect in %d seconds...", RECONNECT_INTERVAL)
        await asyncio.sleep(RECONNECT_INTERVAL)
        
        if self._running:
            await self.connect()

    async def _process_buffer(self, buffer: bytearray) -> bytearray:
        """Process the receive buffer and extract complete packets.
        
        This method looks for complete packets in the buffer and processes them.
        Returns the remaining unprocessed bytes.
        
        CUSTOMIZE THIS: Update the packet detection logic based on your
        spa's actual protocol format.
        """
        # Example packet structure (PLACEHOLDER - adjust to real protocol):
        # [START_BYTE] [LENGTH] [MSG_TYPE] [DATA...] [CHECKSUM] [END_BYTE]
        
        while len(buffer) >= 6:  # Minimum packet size
            # Look for packet start
            start_idx = -1
            for i, byte in enumerate(buffer):
                if byte == PACKET_START:
                    start_idx = i
                    break
            
            if start_idx == -1:
                # No start byte found, clear buffer
                buffer.clear()
                break
            
            # Remove any garbage before start byte
            if start_idx > 0:
                buffer = buffer[start_idx:]
            
            if len(buffer) < 2:
                break
            
            # Get packet length (PLACEHOLDER - adjust to real protocol)
            packet_length = buffer[1]
            
            if len(buffer) < packet_length:
                # Incomplete packet, wait for more data
                break
            
            # Extract complete packet
            packet = bytes(buffer[:packet_length])
            buffer = buffer[packet_length:]
            
            # Parse the packet
            self._parse_status_packet(packet)
        
        return buffer

    def _parse_status_packet(self, packet: bytes) -> None:
        """Parse a status packet and update the spa state.
        
        CUSTOMIZE THIS: Update the byte positions and value interpretations
        based on your spa's actual protocol.
        
        Common Sundance/Jacuzzi packet structure hints:
        - Temperature values are often raw bytes where value = byte_value / 2 (for °F)
        - Or might need conversion: temp_c = (raw_value - 32) * 5 / 9
        - Pump/light states are usually single bits in a status byte
        
        Example packet structure from HyperActiveJ/Jacuzzi-RS485:
        - Byte 0: Start marker (0x7E)
        - Byte 1: Packet length
        - Byte 2: Message type
        - Byte 3-4: Source/destination addresses
        - Byte 5+: Data payload
        - Last byte: Checksum
        """
        if len(packet) < 10:
            _LOGGER.debug("Packet too short to parse: %s", packet.hex())
            return
        
        try:
            msg_type = packet[2] if len(packet) > 2 else 0
            
            # Only process status messages (PLACEHOLDER value)
            if msg_type == MSG_STATUS:
                # Extract temperature values (PLACEHOLDER positions and conversion)
                # Many spas report temp as raw byte / 2 for Fahrenheit
                raw_current = packet[POS_CURRENT_TEMP] if len(packet) > POS_CURRENT_TEMP else 0
                raw_target = packet[POS_TARGET_TEMP] if len(packet) > POS_TARGET_TEMP else 0
                
                # Convert to Celsius (assuming input is Fahrenheit * 2)
                # Adjust this formula based on your spa's encoding
                self.state.current_temp = self._raw_to_celsius(raw_current)
                self.state.target_temp = self._raw_to_celsius(raw_target)
                
                # Extract pump/light/heating states (PLACEHOLDER bit positions)
                status_byte = packet[POS_HEATING_STATE] if len(packet) > POS_HEATING_STATE else 0
                self.state.is_heating = bool(status_byte & 0x01)  # Bit 0 = heating
                
                pump_byte = packet[POS_PUMP1_STATE] if len(packet) > POS_PUMP1_STATE else 0
                self.state.pump1_on = bool(pump_byte & 0x01)  # Bit 0 = pump1
                self.state.pump2_on = bool(pump_byte & 0x02)  # Bit 1 = pump2
                
                light_byte = packet[POS_LIGHT_STATE] if len(packet) > POS_LIGHT_STATE else 0
                self.state.light_on = bool(light_byte & 0x01)  # Bit 0 = light
                
                import time
                self.state.last_update = time.time()
                
                _LOGGER.debug(
                    "Parsed state: temp=%.1f°C, target=%.1f°C, heating=%s, "
                    "pump1=%s, pump2=%s, light=%s",
                    self.state.current_temp,
                    self.state.target_temp,
                    self.state.is_heating,
                    self.state.pump1_on,
                    self.state.pump2_on,
                    self.state.light_on,
                )
                
                self._notify_callbacks()
                
        except Exception as err:
            _LOGGER.warning("Failed to parse status packet: %s - %s", packet.hex(), err)

    def _raw_to_celsius(self, raw_value: int) -> float:
        """Convert raw temperature byte to Celsius.
        
        CUSTOMIZE THIS: Adjust the conversion formula based on your spa's encoding.
        
        Common encodings:
        - raw_value / 2 = Fahrenheit, then convert to Celsius
        - raw_value directly in Fahrenheit
        - raw_value directly in Celsius * 2
        """
        # Assuming raw value is Fahrenheit * 2
        fahrenheit = raw_value / 2.0
        celsius = (fahrenheit - 32) * 5 / 9
        return round(celsius, 1)

    def _celsius_to_raw(self, celsius: float) -> int:
        """Convert Celsius temperature to raw byte value.
        
        CUSTOMIZE THIS: Adjust based on your spa's encoding.
        """
        fahrenheit = (celsius * 9 / 5) + 32
        return int(fahrenheit * 2)

    def _calculate_checksum(self, data: bytes) -> int:
        """Calculate checksum for outgoing packet.
        
        CUSTOMIZE THIS: Implement the actual checksum algorithm used by your spa.
        Common methods:
        - Simple XOR of all bytes
        - Sum of all bytes modulo 256
        - CRC-8 or CRC-16
        """
        # Example: Simple XOR checksum (PLACEHOLDER)
        checksum = 0
        for byte in data:
            checksum ^= byte
        return checksum

    def _build_command(self, msg_type: int, payload: bytes = b"") -> bytes:
        """Build a command packet to send to the spa.
        
        CUSTOMIZE THIS: Adjust the packet structure based on your spa's protocol.
        
        Example structure:
        [START] [LENGTH] [MSG_TYPE] [SRC] [DST] [PAYLOAD...] [CHECKSUM] [END]
        """
        # Build packet without checksum
        src_addr = 0x0A  # PLACEHOLDER - your controller address
        dst_addr = 0x00  # PLACEHOLDER - spa main board address
        
        packet_data = bytes([msg_type, src_addr, dst_addr]) + payload
        length = len(packet_data) + 3  # +3 for start, length, checksum
        
        packet = bytes([PACKET_START, length]) + packet_data
        checksum = self._calculate_checksum(packet)
        packet = packet + bytes([checksum, PACKET_END])
        
        return packet

    async def _send_command(self, command: bytes) -> bool:
        """Send a command to the spa."""
        async with self._lock:
            if not self._writer or not self.state.connected:
                _LOGGER.error("Cannot send command: not connected")
                return False
            
            try:
                _LOGGER.debug("Sending command: %s", command.hex())
                self._writer.write(command)
                await self._writer.drain()
                return True
            except Exception as err:
                _LOGGER.error("Failed to send command: %s", err)
                self.state.connected = False
                self._notify_callbacks()
                return False

    async def set_target_temperature(self, temperature: float) -> bool:
        """Set the target water temperature.
        
        Args:
            temperature: Target temperature in Celsius
        """
        raw_temp = self._celsius_to_raw(temperature)
        payload = bytes([raw_temp])
        command = self._build_command(MSG_SET_TEMP, payload)
        
        _LOGGER.info("Setting target temperature to %.1f°C (raw: %d)", temperature, raw_temp)
        
        if await self._send_command(command):
            # Optimistically update local state
            self.state.target_temp = temperature
            self._notify_callbacks()
            return True
        return False

    async def toggle_pump1(self) -> bool:
        """Toggle pump 1 on/off."""
        command = self._build_command(MSG_TOGGLE_PUMP1)
        
        _LOGGER.info("Toggling pump 1")
        
        if await self._send_command(command):
            # Optimistically update local state
            self.state.pump1_on = not self.state.pump1_on
            self._notify_callbacks()
            return True
        return False

    async def toggle_pump2(self) -> bool:
        """Toggle pump 2 on/off."""
        command = self._build_command(MSG_TOGGLE_PUMP2)
        
        _LOGGER.info("Toggling pump 2")
        
        if await self._send_command(command):
            # Optimistically update local state
            self.state.pump2_on = not self.state.pump2_on
            self._notify_callbacks()
            return True
        return False

    async def toggle_light(self) -> bool:
        """Toggle the spa light on/off."""
        command = self._build_command(MSG_TOGGLE_LIGHT)
        
        _LOGGER.info("Toggling light")
        
        if await self._send_command(command):
            # Optimistically update local state
            self.state.light_on = not self.state.light_on
            self._notify_callbacks()
            return True
        return False

    async def set_pump1(self, on: bool) -> bool:
        """Set pump 1 to a specific state."""
        if self.state.pump1_on != on:
            return await self.toggle_pump1()
        return True

    async def set_pump2(self, on: bool) -> bool:
        """Set pump 2 to a specific state."""
        if self.state.pump2_on != on:
            return await self.toggle_pump2()
        return True

    async def set_light(self, on: bool) -> bool:
        """Set light to a specific state."""
        if self.state.light_on != on:
            return await self.toggle_light()
        return True
