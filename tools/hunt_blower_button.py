"""Full scan 200-255: find buttons that change d[4] blower-relevant bits."""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

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
LOG = Path(__file__).resolve().parents[2] / "debug-b00787.log"


def build(btn: int, mtype: int = 0xCC, ch: int = CMD_CHANNEL) -> bytes:
    msg = bytearray(9)
    msg[0] = M_STARTEND
    msg[1] = 7
    msg[2] = ch
    msg[3] = 0xBF
    msg[4] = mtype
    msg[5] = btn & 0xFF
    msg[6] = 0
    msg[7] = calc_cs(msg[1:7], 6)
    msg[8] = M_STARTEND
    return bytes(msg)


def snap(d: list[int]) -> dict:
    return {
        "p1": bool((d[2] >> 4) & 1),
        "p2": bool((d[1] >> 2) & 1),
        "d1": d[1],
        "d2": d[2],
        "d4": d[4],
        "d7": d[7] if len(d) > 7 else None,
        "d13": d[13],
        "d14": d[14],
    }


async def probe(btn: int, mtype: int) -> dict | None:
    reader, writer = await asyncio.open_connection(HOST, PORT)
    pending: list[tuple[int, bytes]] = []
    before = None
    after = None
    sent = False

    async def recv() -> None:
        nonlocal before, after, sent
        while True:
            msg = await read_msg(reader)
            if msg is None:
                continue
            if msg[4] == CLEAR_TO_SEND and pending and pending[0][0] == msg[2]:
                writer.write(pending.pop(0)[1])
                await writer.drain()
                sent = True
            elif msg[4] in (STATUS_UPDATE, STATUS_UPDATE_ALT):
                d = xormsg(msg[5 : len(msg) - 2])
                if len(d) < 15:
                    continue
                cur = snap(d)
                if before is None:
                    before = cur
                else:
                    after = cur

    task = asyncio.create_task(recv())
    await asyncio.sleep(0.8)
    pending.append((CMD_CHANNEL, build(btn, mtype)))
    await asyncio.sleep(2.5)
    task.cancel()
    writer.close()

    if not before or not after or before == after:
        return None
    return {"btn": btn, "mtype": mtype, "sent": sent, "before": before, "after": after}


async def main() -> None:
    hits = []
    for mtype in (0xCC, 0x17, 0x11):
        for btn in range(200, 256):
            try:
                r = await probe(btn, mtype)
            except Exception as exc:
                print(f"ERR btn={btn} mtype=0x{mtype:02X}: {exc}")
                continue
            if r:
                hits.append(r)
                line = json.dumps(
                    {
                        "sessionId": "b00787",
                        "runId": "blower-hunt",
                        "hypothesisId": "H14",
                        "message": "status_change",
                        "data": r,
                        "timestamp": int(time.time() * 1000),
                    }
                )
                with open(LOG, "a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
                b, a = r["before"], r["after"]
                diff = {k: [b[k], a[k]] for k in b if b[k] != a[k]}
                print(f"btn {btn} mtype 0x{mtype:02X}: {diff}")
            await asyncio.sleep(0.25)
    print(f"Total hits: {len(hits)}")


if __name__ == "__main__":
    asyncio.run(main())
