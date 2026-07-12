"""Try menu navigation sequences then blower candidates."""
from __future__ import annotations

import asyncio

from spa_test import CLEAR_TO_SEND, CMD_CHANNEL, build_cc, read_msg, xormsg, STATUS_UPDATE, STATUS_UPDATE_ALT

HOST = "192.168.178.54"
PORT = 8899


async def run_seq(name: str, buttons: list[int], pause: float = 1.2) -> None:
    reader, writer = await asyncio.open_connection(HOST, PORT)
    pending: list[tuple[int, bytes]] = []
    last_d4 = None

    async def recv() -> None:
        nonlocal last_d4
        while True:
            msg = await read_msg(reader)
            if msg is None:
                continue
            if msg[4] == CLEAR_TO_SEND and pending and pending[0][0] == msg[2]:
                writer.write(pending.pop(0)[1])
                await writer.drain()
            elif msg[4] in (STATUS_UPDATE, STATUS_UPDATE_ALT):
                d = xormsg(msg[5 : len(msg) - 2])
                if len(d) >= 15:
                    last_d4 = d[4]

    task = asyncio.create_task(recv())
    await asyncio.sleep(1)
    for btn in buttons:
        pending.append((CMD_CHANNEL, build_cc(btn)))
        await asyncio.sleep(pause)
    await asyncio.sleep(1)
    task.cancel()
    writer.close()
    print(f"{name}: done, last d4={last_d4}")


async def main() -> None:
    # Turn pumps off first
    print("=== pumps off ===")
    await run_seq("poff", [228, 229])
    await asyncio.sleep(2)

    candidates = [
        ("direct_243_cc", [243]),
        ("direct_243_x3", [243, 243, 243]),
        ("menu254_then_243", [254, 254, 243]),
        ("240_241_243", [240, 241, 243]),
        ("238_239_243", [238, 239, 243]),
        ("230_243", [230, 243]),
        ("231_243", [231, 243]),
        ("233_243", [233, 243]),
        ("234_243", [234, 243]),
        ("235_243", [235, 243]),
        ("237_243", [237, 243]),
    ]
    for name, btns in candidates:
        await run_seq(name, btns)
        await asyncio.sleep(2)


if __name__ == "__main__":
    asyncio.run(main())
