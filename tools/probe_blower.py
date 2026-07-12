"""Probe blower control variants – log status diffs for each attempt."""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from spa_test import (
    CLEAR_TO_SEND,
    CMD_CHANNEL,
    M_STARTEND,
    STATUS_UPDATE,
    STATUS_UPDATE_ALT,
    build_cc,
    calc_cs,
    read_msg,
    xormsg,
)

HOST = "192.168.178.54"
PORT = 8899
LOG = Path(__file__).resolve().parents[2] / "debug-b00787.log"


def agent_log(msg: str, data: dict, hid: str) -> None:
    line = json.dumps(
        {
            "sessionId": "b00787",
            "runId": "blower-scan",
            "hypothesisId": hid,
            "location": "probe_blower.py",
            "message": msg,
            "data": data,
            "timestamp": int(time.time() * 1000),
        },
        ensure_ascii=False,
    )
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    print(f"[{hid}] {msg}: {data}")


def build_pkt(channel: int, mtype: int, b5: int, b6: int = 0) -> bytes:
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


def status_snap(d: list[int]) -> dict:
    return {
        "p1": bool((d[2] >> 4) & 1),
        "p2": bool((d[1] >> 2) & 1),
        "d1": d[1],
        "d2": d[2],
        "d4": d[4],
        "d13": d[13],
        "d14": d[14],
        "blower_guess": bool((d[4] >> 2) & 0x0C) or bool((d[13] >> 2) & 3),
    }


async def try_variant(label: str, pkt: bytes, queue_ch: int, hid: str) -> None:
    reader, writer = await asyncio.open_connection(HOST, PORT)
    pending: list[tuple[int, bytes]] = []
    before: dict | None = None
    after: dict | None = None
    sent = False

    async def recv() -> None:
        nonlocal before, after, sent
        while True:
            msg = await read_msg(reader)
            if msg is None:
                continue
            mtype = msg[4]
            ch = msg[2]
            if mtype == CLEAR_TO_SEND and pending and pending[0][0] == ch:
                _, out = pending.pop(0)
                writer.write(out)
                await writer.drain()
                sent = True
            elif mtype in (STATUS_UPDATE, STATUS_UPDATE_ALT):
                d = xormsg(msg[5 : len(msg) - 2])
                if len(d) < 15:
                    continue
                cur = status_snap(d)
                if before is None:
                    before = cur
                else:
                    after = cur

    task = asyncio.create_task(recv())
    await asyncio.sleep(1.0)
    pending.append((queue_ch, pkt))
    await asyncio.sleep(3.0)
    task.cancel()
    writer.close()

    diff = {}
    if before and after:
        diff = {k: [before[k], after[k]] for k in before if before[k] != after[k]}
    agent_log(
        label,
        {
            "pkt_hex": pkt.hex(),
            "queue_ch": queue_ch,
            "sent": sent,
            "before": before,
            "after": after,
            "diff": diff,
        },
        hid,
    )


async def main() -> None:
    # H6: sanity – pump1 should change status
    await try_variant(
        "pump1_cc_228",
        build_cc(228, CMD_CHANNEL),
        CMD_CHANNEL,
        "H6",
    )
    await asyncio.sleep(1)

    variants = [
        ("blower_243_cc", build_pkt(CMD_CHANNEL, 0xCC, 243), CMD_CHANNEL, "H7"),
        ("blower_243_17", build_pkt(CMD_CHANNEL, 0x17, 243), CMD_CHANNEL, "H8"),
        ("toggle11_0c", build_pkt(CMD_CHANNEL, 0x11, 0x0C, 0), CMD_CHANNEL, "H9"),
        ("toggle11_0c_b6", build_pkt(CMD_CHANNEL, 0x11, 0x0C, 0x00), CMD_CHANNEL, "H9"),
        ("btn12_cc", build_pkt(CMD_CHANNEL, 0xCC, 12), CMD_CHANNEL, "H10"),
        ("btn244_cc", build_pkt(CMD_CHANNEL, 0xCC, 244), CMD_CHANNEL, "H10"),
        ("btn245_cc", build_pkt(CMD_CHANNEL, 0xCC, 245), CMD_CHANNEL, "H10"),
        ("btn246_cc", build_pkt(CMD_CHANNEL, 0xCC, 246), CMD_CHANNEL, "H10"),
        ("btn247_cc", build_pkt(CMD_CHANNEL, 0xCC, 247), CMD_CHANNEL, "H10"),
    ]
    for label, pkt, qch, hid in variants:
        await try_variant(label, pkt, qch, hid)
        await asyncio.sleep(0.8)


if __name__ == "__main__":
    asyncio.run(main())
