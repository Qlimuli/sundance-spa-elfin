"""Sniff RS485: panel temp buttons + set_temp changes."""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime
from pathlib import Path

from spa_test import STATUS_UPDATE, STATUS_UPDATE_ALT, read_msg, xormsg

HOST = "192.168.178.54"
PORT = 8899
LOG = Path(__file__).resolve().parents[2] / "debug-b00787.log"
CC_TYPES = (0xCC, 0x17)


def agent_log(msg: str, data: dict, hid: str) -> None:
    line = json.dumps(
        {
            "sessionId": "b00787",
            "runId": "temp-sniff",
            "hypothesisId": hid,
            "location": "sniff_temp_panel.py",
            "message": msg,
            "data": data,
            "timestamp": int(time.time() * 1000),
        },
        ensure_ascii=False,
    )
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    print(f"{datetime.now():%H:%M:%S} {msg} {data}")


def decode_set_temp(raw: int) -> float:
    if raw >= 80:
        return round((raw - 32) * 5 / 9, 1)
    return raw / 2.0


async def main(seconds: float = 120) -> None:
    reader, writer = await asyncio.open_connection(HOST, PORT)
    last_set: float | None = None
    last_raw: int | None = None
    agent_log("listen_start", {"seconds": seconds, "host": HOST}, "H21")
    print(
        "\n>>> Temperatur am Panel: 2x Wärmer (+0.5), dann 2x Kühler (-0.5) <<<\n"
    )
    end = asyncio.get_event_loop().time() + seconds
    while asyncio.get_event_loop().time() < end:
        msg = await read_msg(reader)
        if msg is None or len(msg) < 7:
            continue
        mtype = msg[4]
        if mtype in CC_TYPES:
            entry = {
                "ch": msg[2],
                "mtype": mtype,
                "btn": msg[5],
                "b6": msg[6],
                "decoded_cc": msg[5] ^ msg[6] ^ 1,
                "raw": msg.hex(),
            }
            agent_log("panel_cc", entry, "H21")
            continue
        if mtype in (STATUS_UPDATE, STATUS_UPDATE_ALT):
            d = xormsg(msg[5 : len(msg) - 2])
            if len(d) < 15:
                continue
            raw_d8 = d[8]
            set_t = decode_set_temp(raw_d8)
            if set_t != last_set or raw_d8 != last_raw:
                agent_log(
                    "set_temp_change",
                    {
                        "set_temp": set_t,
                        "raw_d8": raw_d8,
                        "display": d[13],
                        "p1": bool((d[2] >> 4) & 1),
                        "p2": bool((d[1] >> 2) & 1),
                    },
                    "H22",
                )
                last_set = set_t
                last_raw = raw_d8
    writer.close()
    agent_log("listen_end", {}, "H21")


if __name__ == "__main__":
    asyncio.run(main())
