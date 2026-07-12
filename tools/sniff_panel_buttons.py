"""Sniff RS485 when user presses physical BLOWER button on spa panel."""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime
from pathlib import Path

from spa_test import read_msg

HOST = "192.168.178.54"
PORT = 8899
LOG = Path(__file__).resolve().parents[2] / "debug-b00787.log"
CC, CC_ALT = 0xCC, 0x17


def log(msg: str, data: dict) -> None:
    line = json.dumps(
        {
            "sessionId": "b00787",
            "runId": "panel-sniff",
            "hypothesisId": "H15",
            "location": "sniff_panel_buttons.py",
            "message": msg,
            "data": data,
            "timestamp": int(time.time() * 1000),
        },
        ensure_ascii=False,
    )
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    print(f"{datetime.now():%H:%M:%S} {msg} {data}")


async def main(seconds: float = 90) -> None:
    reader, writer = await asyncio.open_connection(HOST, PORT)
    log("listen_start", {"seconds": seconds, "host": HOST})
    print(f"\n>>> JETZT die BLUBBER-Taste am Spa-Panel drücken (Ein, dann Aus) <<<\n")
    end = asyncio.get_event_loop().time() + seconds
    while asyncio.get_event_loop().time() < end:
        msg = await read_msg(reader)
        if msg is None or len(msg) < 7:
            continue
        mtype = msg[4]
        if mtype not in (CC, CC_ALT):
            continue
        entry = {
            "ch": msg[2],
            "mtype": mtype,
            "btn": msg[5],
            "b6": msg[6],
            "decoded_cc": msg[5] ^ msg[6] ^ 1,
            "raw": msg.hex(),
        }
        log("panel_button", entry)
    writer.close()
    log("listen_end", {})


if __name__ == "__main__":
    asyncio.run(main())
