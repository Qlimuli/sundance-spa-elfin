"""Wide scan for blower – watch d[4] flag byte (Balboa blower = 0x0C)."""
from __future__ import annotations

import asyncio

from spa_test import (
    CLEAR_TO_SEND,
    CMD_CHANNEL,
    STATUS_UPDATE,
    STATUS_UPDATE_ALT,
    calc_cs,
    M_STARTEND,
    read_msg,
    xormsg,
)

HOST = "192.168.178.54"
PORT = 8899


def build(channel: int, mtype: int, b5: int, b6: int = 0) -> bytes:
    msg = bytearray(9)
    msg[0] = M_STARTEND
    msg[1] = 7
    msg[2] = channel
    msg[3] = 0xBF
    msg[4] = mtype
    msg[5] = b5 & 0xFF
    msg[6] = b6 & 0xFF
    msg[7] = calc_cs(msg[1:7], 6)
    msg[8] = M_STARTEND
    return bytes(msg)


async def probe(btn: int, mtype: int) -> list[str]:
    reader, writer = await asyncio.open_connection(HOST, PORT)
    pending: list[tuple[int, bytes]] = []
    base: list[int] | None = None
    hits: list[str] = []

    async def recv() -> None:
        nonlocal base
        while True:
            msg = await read_msg(reader)
            if msg is None:
                continue
            if msg[4] == CLEAR_TO_SEND and pending and pending[0][0] == msg[2]:
                writer.write(pending.pop(0)[1])
                await writer.drain()
            elif msg[4] in (STATUS_UPDATE, STATUS_UPDATE_ALT):
                d = xormsg(msg[5 : len(msg) - 2])
                if len(d) < 15:
                    continue
                if base is None:
                    base = list(d)
                else:
                    ch = [
                        (i, base[i], d[i])
                        for i in range(len(d))
                        if base[i] != d[i]
                    ]
                    if ch:
                        hits.extend(
                            f"i{i}:{a}->{b}" for i, a, b in ch
                        )
                        base = list(d)

    task = asyncio.create_task(recv())
    await asyncio.sleep(0.7)
    pending.append((CMD_CHANNEL, build(CMD_CHANNEL, mtype, btn)))
    await asyncio.sleep(2.0)
    task.cancel()
    writer.close()
    return hits


async def main() -> None:
    for mtype in (0xCC, 0x17):
        print(f"=== mtype 0x{mtype:02X} ===")
        for btn in range(230, 256):
            try:
                hits = await probe(btn, mtype)
            except Exception as exc:
                print(f"btn {btn}: ERR {exc}")
                continue
            if hits:
                print(f"btn {btn}: {' | '.join(hits[:8])}")
            await asyncio.sleep(0.3)


if __name__ == "__main__":
    asyncio.run(main())
