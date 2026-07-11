"""Scan CC button codes and report status/light changes."""
import asyncio
import sys

from spa_test import (
    CLEAR_TO_SEND,
    STATUS_UPDATE,
    STATUS_UPDATE_ALT,
    build_cc,
    read_msg,
    xormsg,
)

HOST = "192.168.178.54"
PORT = 8899
CHANNEL = 0x10


def status_tuple(d: list[int]) -> tuple:
    return (
        round(d[8] / 2, 1),
        d[13],
        bool((d[2] >> 4) & 1),
        bool((d[1] >> 2) & 1),
        bool((d[1] >> 6) & 1),
    )


def light_tuple(d: list[int]) -> tuple:
    return (d[1] > 0, d[1], d[4], d[2], d[6], d[8])


async def probe_button(btn: int) -> list[str]:
    reader, writer = await asyncio.open_connection(HOST, PORT)
    pending: list[tuple[int, bytes]] = []
    base_s = None
    base_l = None
    hits: list[str] = []

    async def recv() -> None:
        nonlocal base_s, base_l
        while True:
            msg = await read_msg(reader)
            if msg is None:
                continue
            mtype = msg[4]
            ch = msg[2]
            if mtype == CLEAR_TO_SEND and pending and pending[0][0] == ch:
                _, pkt = pending.pop(0)
                writer.write(pkt)
                await writer.drain()
            elif mtype in (STATUS_UPDATE, STATUS_UPDATE_ALT):
                d = xormsg(msg[5 : len(msg) - 2])
                if len(d) < 15:
                    continue
                cur = status_tuple(d)
                if base_s is None:
                    base_s = cur
                elif cur != base_s:
                    hits.append(f"status {base_s} -> {cur}")
                    base_s = cur
            elif mtype in (0xCA, 0x23):
                d = xormsg(msg[5 : len(msg) - 2])
                if len(d) < 10:
                    continue
                cur = light_tuple(d)
                if base_l is None:
                    base_l = cur
                elif cur != base_l:
                    hits.append(f"light {base_l} -> {cur}")
                    base_l = cur

    task = asyncio.create_task(recv())
    await asyncio.sleep(0.8)
    pending.append((CHANNEL, build_cc(btn, CHANNEL)))
    await asyncio.sleep(2.0)
    task.cancel()
    writer.close()
    return hits


async def main(start: int, end: int) -> None:
    for btn in range(start, end + 1):
        try:
            hits = await probe_button(btn)
        except Exception as exc:
            print(f"btn {btn}: ERROR {exc}")
            continue
        if hits:
            print(f"btn {btn}: " + " | ".join(hits))


if __name__ == "__main__":
    start = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    end = int(sys.argv[2]) if len(sys.argv) > 2 else 255
    asyncio.run(main(start, end))
