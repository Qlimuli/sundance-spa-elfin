"""Standalone spa protocol test script (no Home Assistant dependency)."""
import asyncio

M_STARTEND = 0x7E
CLEAR_TO_SEND = 0x06
STATUS_UPDATE = 0xC4
LIGHTS_UPDATE = 0xCA
STATUS_UPDATE_ALT = 0x16
LIGHTS_UPDATE_ALT = 0x23
CC_REQ = 0xCC
CMD_CHANNEL = 0x10
CH_BROADCAST = 0xFE
MSG_CHANNEL_REQ = 0x01
MSG_CHANNEL_ASSIGN = 0x02


def calc_cs(data, length):
    crc = 0xB5
    for cur in range(length):
        for i in range(8):
            bit = crc & 0x80
            crc = ((crc << 1) & 0xFF) | ((data[cur] >> (7 - i)) & 1)
            if bit:
                crc ^= 0x07
        crc &= 0xFF
    for i in range(8):
        bit = crc & 0x80
        crc = (crc << 1) & 0xFF
        if bit:
            crc ^= 0x07
    return (crc ^ 0x02) & 0xFF


def xormsg(data):
    return [data[i] ^ data[i + 1] ^ 1 for i in range(0, len(data) - 1, 2)]


def build_cc(btn, channel=CMD_CHANNEL):
    msg = bytearray(9)
    msg[0] = M_STARTEND
    msg[1] = 7
    msg[2] = channel
    msg[3] = 0xBF
    msg[4] = CC_REQ
    msg[5] = btn & 0xFF
    msg[6] = 0
    msg[7] = calc_cs(msg[1:7], 6)
    msg[8] = M_STARTEND
    return bytes(msg)


async def read_msg(reader):
    hf, rlen = False, 0
    while not hf or rlen == 0:
        b = await asyncio.wait_for(reader.readexactly(1), 15)
        if b[0] == M_STARTEND:
            hf = True
        elif hf:
            rlen = b[0]
    rest = await asyncio.wait_for(reader.readexactly(rlen), 5)
    full = bytes([M_STARTEND, rlen]) + rest
    if calc_cs(full[1:], rlen - 1) != full[-2]:
        return None
    return full


async def run_sequence(name, buttons, host="192.168.178.54", port=8899):
    reader, writer = await asyncio.open_connection(host, port)
    pending = []
    last = None

    async def recv():
        nonlocal last
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
                if len(d) >= 15:
                    cur = {"set": d[8] / 2, "display": d[13], "raw8": d[8]}
                    if cur != last:
                        print(
                            f"{name}: display={cur['display']} "
                            f"set={cur['set']} raw8={cur['raw8']}"
                        )
                        last = cur
            elif mtype in (LIGHTS_UPDATE, LIGHTS_UPDATE_ALT):
                d = xormsg(msg[5 : len(msg) - 2])
                if len(d) >= 10:
                    print(f"{name}: light on={d[1] > 0} bright={d[1]} mode={d[4]}")

    task = asyncio.create_task(recv())
    await asyncio.sleep(1)
    for btn in buttons:
        pending.append((CMD_CHANNEL, build_cc(btn)))
        await asyncio.sleep(1.5)
    await asyncio.sleep(1)
    task.cancel()
    writer.close()


async def main():
    print("=== Menu exit ===")
    await run_sequence("menu", [254] * 5)
    print("=== Temp ===")
    await run_sequence("temp", [225, 225, 226, 226, 226])
    print("=== Light ===")
    await run_sequence("light", [241] * 8)
    print("=== Color ===")
    await run_sequence("color", [242] * 8)


if __name__ == "__main__":
    asyncio.run(main())
