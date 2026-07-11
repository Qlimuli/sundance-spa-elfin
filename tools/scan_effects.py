"""Find buttons that change display mode or set temperature."""
import asyncio

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


async def probe(btn: int, channel: int = 0x10) -> dict:
    reader, writer = await asyncio.open_connection(HOST, PORT)
    pending: list[tuple[int, bytes]] = []
    before = None
    after = None
    light_before = None
    light_after = None

    async def recv() -> None:
        nonlocal before, after, light_before, light_after
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
                snap = {
                    "set_raw": d[8],
                    "set_c2": d[8] / 2,
                    "set_f": d[8],
                    "display": d[13],
                    "p1": bool((d[2] >> 4) & 1),
                    "p2": bool((d[1] >> 2) & 1),
                }
                if before is None:
                    before = snap
                else:
                    after = snap
            elif mtype in (0xCA, 0x23):
                d = xormsg(msg[5 : len(msg) - 2])
                if len(d) < 10:
                    continue
                snap = {
                    "on": d[1] > 0,
                    "bright": d[1],
                    "mode": d[4],
                }
                if light_before is None:
                    light_before = snap
                else:
                    light_after = snap

    task = asyncio.create_task(recv())
    await asyncio.sleep(0.8)
    pending.append((channel, build_cc(btn, channel)))
    await asyncio.sleep(2.0)
    task.cancel()
    writer.close()
    return {
        "btn": btn,
        "before": before,
        "after": after,
        "light_before": light_before,
        "light_after": light_after,
    }


def changed(result: dict) -> bool:
    b, a = result["before"], result["after"]
    lb, la = result["light_before"], result["light_after"]
    if b and a and b != a:
        return True
    if lb and la and lb != la:
        return True
    return False


async def main(start: int, end: int) -> None:
    for btn in range(start, end + 1):
        try:
            result = await probe(btn)
        except Exception as exc:
            print(f"{btn}: error {exc}")
            continue
        if not changed(result):
            continue
        print(f"btn {btn}:")
        if result["before"] != result["after"]:
            print(f"  status: {result['before']} -> {result['after']}")
        if result["light_before"] != result["light_after"]:
            print(f"  light:  {result['light_before']} -> {result['light_after']}")


if __name__ == "__main__":
    asyncio.run(main(180, 255))
