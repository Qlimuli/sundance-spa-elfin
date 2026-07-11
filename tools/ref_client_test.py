"""Reference-style Sundance RS485 client for live protocol tests."""
from __future__ import annotations

import asyncio

from spa_test import (
    CLEAR_TO_SEND,
    LIGHTS_UPDATE,
    LIGHTS_UPDATE_ALT,
    M_STARTEND,
    STATUS_UPDATE,
    STATUS_UPDATE_ALT,
    build_cc,
    calc_cs,
    read_msg,
    xormsg,
)

CLIENT_CLEAR_TO_SEND = 0x00
CHANNEL_ASSIGNMENT_REQ = 0x01
CHANNEL_ASSIGNMENT_RESP = 0x02
CHANNEL_ASSIGNMENT_ACK = 0x03
NOTHING_TO_SEND = 0x07
CC_REQ = 0xCC

BTN_TEMP_UP = 225
BTN_TEMP_DOWN = 226
BTN_LIGHT = 241
BTN_LIGHT_COLOR = 242
NO_CHANGE = -1
CHECKS_BEFORE_RETRY = 2


class SundanceTestClient:
    def __init__(self, host: str, port: int = 8899) -> None:
        self.host = host
        self.port = port
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self.channel: int | None = None
        self.queue: asyncio.Queue[bytes] = asyncio.Queue()
        self.discovered: list[int] = []
        self.active: list[int] = []
        self.detect_state = 0
        self.settemp: float | None = None
        self.target_temp = NO_CHANGE
        self.check_counter = 0
        self.light_brightness = 0
        self.light_mode = 0
        self.target_light_brightness = NO_CHANGE
        self.target_light_mode = NO_CHANGE
        self.check_counter_l = 0
        self.sent = 0

    async def connect(self) -> None:
        self.reader, self.writer = await asyncio.open_connection(self.host, self.port)

    async def run(self, seconds: float) -> None:
        assert self.reader and self.writer
        end = asyncio.get_event_loop().time() + seconds
        while asyncio.get_event_loop().time() < end:
            msg = await read_msg(self.reader)
            if msg is None:
                continue
            await self._handle(msg)

    async def _handle(self, data: bytes) -> None:
        assert self.writer
        channel = data[2]
        mtype = data[4]

        if mtype in (STATUS_UPDATE, STATUS_UPDATE_ALT):
            await self._parse_status(data)
        elif mtype in (LIGHTS_UPDATE, LIGHTS_UPDATE_ALT):
            await self._parse_lights(data)
        elif mtype == CLIENT_CLEAR_TO_SEND and self.channel is None and self.detect_state == 5:
            req = bytearray(10)
            req[0] = M_STARTEND
            req[1] = 8
            req[2] = 0xFE
            req[3] = 0xBF
            req[4] = CHANNEL_ASSIGNMENT_REQ
            req[5] = 0x02
            req[6] = 0xF1
            req[7] = 0x73
            req[8] = calc_cs(req[1:8], 7)
            req[9] = M_STARTEND
            self.writer.write(bytes(req))
            await self.writer.drain()
        elif mtype == CHANNEL_ASSIGNMENT_RESP:
            self.channel = data[5]
            print(f"assigned channel 0x{self.channel:02X}")
            ack = bytearray(7)
            ack[0] = M_STARTEND
            ack[1] = 5
            ack[2] = self.channel
            ack[3] = 0xBF
            ack[4] = CHANNEL_ASSIGNMENT_ACK
            ack[5] = calc_cs(ack[1:5], 4)
            ack[6] = M_STARTEND
            self.writer.write(bytes(ack))
            await self.writer.drain()
        elif mtype == CLEAR_TO_SEND:
            if data[2] == self.channel:
                if not self.queue.empty():
                    pkt = self.queue.get_nowait()
                    self.writer.write(pkt)
                    await self.writer.drain()
                    self.sent += 1
                    print(f"sent CC btn={pkt[5]} on 0x{self.channel:02X}")
            if data[2] not in self.discovered:
                self.discovered.append(data[2])
            if mtype == CC_REQ and data[2] not in self.active:
                self.active.append(data[2])
            if self.detect_state < 5:
                self.detect_state += 1
                if self.detect_state == 5 and self.channel is None:
                    self.discovered.sort()
                    for ch in self.discovered:
                        if ch not in self.active:
                            self.channel = ch
                            print(f"picked idle channel 0x{ch:02X}")
                            break

    async def _parse_status(self, data: bytes) -> None:
        d = xormsg(data[5 : len(data) - 2])
        if len(d) < 15:
            return
        settemp = d[8] / 2.0
        if self.settemp != settemp:
            print(f"settemp {self.settemp} -> {settemp} (raw {d[8]})")
        self.settemp = settemp

        if self.check_counter > 0:
            self.check_counter -= 1
        if self.check_counter == 0 and self.target_temp != NO_CHANGE:
            if abs(settemp - self.target_temp) > 0.25:
                btn = BTN_TEMP_DOWN if self.target_temp < settemp else BTN_TEMP_UP
                await self._queue_cc(btn)
                self.check_counter = CHECKS_BEFORE_RETRY
            else:
                print(f"target temp {self.target_temp} reached")
                self.target_temp = NO_CHANGE

    async def _parse_lights(self, data: bytes) -> None:
        d = xormsg(data[5 : len(data) - 2])
        if len(d) < 10:
            return
        bright = d[1]
        mode = d[4]
        if bright != self.light_brightness or mode != self.light_mode:
            print(f"lights bright={bright} mode={mode}")
        self.light_brightness = bright
        self.light_mode = mode

        if self.check_counter_l > 0:
            self.check_counter_l -= 1
        if self.check_counter_l == 0 and self.target_light_mode != NO_CHANGE:
            if self.target_light_mode != mode:
                await self._queue_cc(BTN_LIGHT_COLOR)
                self.check_counter_l = CHECKS_BEFORE_RETRY
            else:
                print(f"target light mode {self.target_light_mode} reached")
                self.target_light_mode = NO_CHANGE
        if self.check_counter_l == 0 and self.target_light_brightness != NO_CHANGE:
            if self.target_light_brightness != bright:
                await self._queue_cc(BTN_LIGHT)
                self.check_counter_l = CHECKS_BEFORE_RETRY
            else:
                print(f"target brightness {self.target_light_brightness} reached")
                self.target_light_brightness = NO_CHANGE

    async def _queue_cc(self, btn: int) -> None:
        if self.channel is None:
            return
        await self.queue.put(build_cc(btn, self.channel))

    async def set_target_temp(self, temp: float, seconds: float = 40) -> None:
        self.target_temp = temp
        self.check_counter = 0
        await self.run(seconds)

    async def set_light_on(self, seconds: float = 20) -> None:
        self.target_light_brightness = 33
        self.check_counter_l = 0
        await self.run(seconds)


async def main() -> None:
    client = SundanceTestClient("192.168.178.54")
    await client.connect()
    print("waiting for channel...")
    await client.run(8)
    print("channel", client.channel, "sent", client.sent)
    print("=== temp to 30 ===")
    await client.set_target_temp(30.0, 45)
    print("=== light on ===")
    await client.set_light_on(25)


if __name__ == "__main__":
    asyncio.run(main())
