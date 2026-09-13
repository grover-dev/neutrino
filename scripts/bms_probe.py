#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["bleak>=0.22"]
# ///
"""Probe a BLE battery BMS: scan, dump GATT, try known poll commands, log notifications.

Usage (uv resolves bleak automatically from the inline metadata above):
    uv run scripts/bms_probe.py scan                    # list nearby BLE devices
    uv run scripts/bms_probe.py dump  [DEV]             # dump services/characteristics
    uv run scripts/bms_probe.py probe [DEV]             # fire known poll cmds, print replies
    uv run scripts/bms_probe.py raw   [DEV] <write-uuid> <notify-uuid> <hex-bytes>
    uv run scripts/bms_probe.py cmd   <cmd-hex> [data-hex]      # one AA55 frame, decoded reply
    uv run scripts/bms_probe.py sweep [start-hex] [end-hex] [wait-s]   # try every cmd byte
    uv run scripts/bms_probe.py variants [cmd-hex] [wait-s]  # same cmd, different framings/checksums

DEV is a MAC address or advertised name; defaults to DEVICE_NAME below.
The `probe` step is what identifies the BMS family: whichever command gets a
reply tells you which decoder to use.
"""

import asyncio
import re
import sys
import time

from bleak import BleakClient, BleakScanner

DEVICE_NAME = "B7522604160102"
DEVICE_MAC = "53:EE:10:0C:35:37"

JBD_BASIC = bytes.fromhex("DDA50300FFFD77")
JBD_CELLS = bytes.fromhex("DDA50400FFFC77")
JK_INFO = bytes.fromhex("AA5590EB97000000000000000000000000000011")
JK_CELLS = bytes.fromhex("AA5590EB96000000000000000000000000000010")
DALY_SOC = bytes.fromhex("A58090080000000000000000BD")
DALY_CELLS = bytes.fromhex("A58095080000000000000000C2")
PACE_ANALOG = b"~25014642E00201FD30\r"
MODBUS_HOLD = bytes.fromhex("010300000026C5C0")

# Known BMS BLE fingerprints: (name, write uuid, notify uuid, [poll commands])
KNOWN = [
    (
        # ADI/Maxim MSDK "Data Transfer Service": transparent serial pipe to the
        # BMS UART, so try every common protocol through it.
        "ADI DATS serial bridge (Dyness 12V)",
        "00002760-08c2-11e1-9073-0e8ac72e0001",
        "00002760-08c2-11e1-9073-0e8ac72e0002",
        [JBD_BASIC, JBD_CELLS, JK_INFO, JK_CELLS, DALY_SOC, DALY_CELLS,
         PACE_ANALOG, MODBUS_HOLD],
    ),
    (
        "JBD / Xiaoxiang",
        "0000ff02-0000-1000-8000-00805f9b34fb",
        "0000ff01-0000-1000-8000-00805f9b34fb",
        [bytes.fromhex("DDA50300FFFD77"), bytes.fromhex("DDA50400FFFC77")],
    ),
    (
        "JK BMS",
        "0000ffe1-0000-1000-8000-00805f9b34fb",
        "0000ffe1-0000-1000-8000-00805f9b34fb",
        # device info (0x97) then cell info (0x96)
        [bytes.fromhex("AA5590EB97000000000000000000000000000011"),
         bytes.fromhex("AA5590EB96000000000000000000000000000010")],
    ),
    (
        "Daly",
        "0000fff2-0000-1000-8000-00805f9b34fb",
        "0000fff1-0000-1000-8000-00805f9b34fb",
        [bytes.fromhex("A58090080000000000000000BD"),   # SOC/V/I
         bytes.fromhex("A58095080000000000000000C2")],  # cell voltages
    ),
    (
        "Dyness FE00 (Pace/Seplos-like?)",
        "0000fe01-0000-1000-8000-00805f9b34fb",
        "0000fe02-0000-1000-8000-00805f9b34fb",
        # Pace ASCII "get analog" for pack 1; and a modbus-style guess
        [b"~25014642E00201FD30\r", bytes.fromhex("010300000026C5C0")],
    ),
]

NOTIFY_WAIT_S = 2.0
MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")

# Serial pipe on the Nations BLE SoC (ADI DATS UUIDs).
PIPE_TX = "00002760-08c2-11e1-9073-0e8ac72e0001"
PIPE_RX = "00002760-08c2-11e1-9073-0e8ac72e0002"


def hexdump(data: bytes) -> str:
    return " ".join(f"{b:02X}" for b in data)


def xor(data: bytes) -> int:
    v = 0
    for b in data:
        v ^= b
    return v


def aa55(cmd: int, data: bytes = b"") -> bytes:
    """Build a frame in the format the BMS replied with: AA 55 cmd len data xor."""
    frame = bytes([0xAA, 0x55, cmd, len(data)]) + data
    return frame + bytes([xor(frame)])


def parse_aa55(frame: bytes):
    """Return (cmd, data, checksum_ok) or None if not an AA55 frame."""
    if len(frame) < 5 or frame[:2] != b"\xaa\x55":
        return None
    cmd, n = frame[2], frame[3]
    data = frame[4:4 + n]
    ok = len(frame) >= 5 + n and xor(frame[:4 + n]) == frame[4 + n]
    return cmd, data, ok


async def resolve(dev: str) -> str:
    """Accept a MAC or an advertised name; return a MAC."""
    if MAC_RE.match(dev):
        return dev
    print(f"looking for {dev!r} ...")
    found = await BleakScanner.find_device_by_name(dev, timeout=15.0)
    if found is None:
        sys.exit(f"device {dev!r} not found (is the app disconnected? BLE only allows one client)")
    print(f"found {dev!r} at {found.address}")
    return found.address


async def scan():
    print("scanning 8s ...")
    devices = await BleakScanner.discover(timeout=8.0, return_adv=True)
    for dev, adv in devices.values():
        mark = " <==" if dev.name == DEVICE_NAME else ""
        print(f"{dev.address}  rssi={adv.rssi:4}  name={dev.name!r}  svc={adv.service_uuids}{mark}")


async def dump(dev: str):
    mac = await resolve(dev)
    async with BleakClient(mac) as client:
        print(f"connected to {mac}")
        for svc in client.services:
            print(f"[svc] {svc.uuid}  {svc.description}")
            for ch in svc.characteristics:
                print(f"    [chr] {ch.uuid}  {','.join(ch.properties)}  {ch.description}")
                if "read" in ch.properties:
                    try:
                        val = bytes(await client.read_gatt_char(ch))
                        print(f"        = {val!r}  [{hexdump(val)}]")
                    except Exception as e:  # noqa: BLE001
                        print(f"        read failed: {e}")
                for d in ch.descriptors:
                    print(f"        [desc] {d.uuid}")


async def _send_and_listen(client, write_uuid, notify_uuid, cmd):
    got = []

    def on_notify(_, data: bytearray):
        got.append(bytes(data))
        print(f"    <- ({len(data):3}) {hexdump(data)}")

    await client.start_notify(notify_uuid, on_notify)
    print(f"    -> ({len(cmd):3}) {hexdump(cmd)}")
    await client.write_gatt_char(write_uuid, cmd, response=False)
    await asyncio.sleep(NOTIFY_WAIT_S)
    await client.stop_notify(notify_uuid)
    return got


async def probe(dev: str):
    mac = await resolve(dev)
    async with BleakClient(mac) as client:
        print(f"connected to {mac}")
        have = {ch.uuid for svc in client.services for ch in svc.characteristics}
        hits = []
        for name, w, n, cmds in KNOWN:
            if w not in have or n not in have:
                print(f"[skip] {name}: chars not present")
                continue
            print(f"[try]  {name}")
            for cmd in cmds:
                try:
                    replies = await _send_and_listen(client, w, n, cmd)
                except Exception as e:  # noqa: BLE001
                    print(f"    error: {e}")
                    continue
                if replies:
                    print(f"[hit]  {name}: {hexdump(cmd)} got {len(replies)} reply(s)")
                    hits.append((name, cmd))
        if not hits:
            print("no known command got a reply; sniff the app (HCI snoop log) next")


async def raw(dev: str, write_uuid: str, notify_uuid: str, hexstr: str):
    mac = await resolve(dev)
    cmd = bytes.fromhex(hexstr.replace(" ", ""))
    async with BleakClient(mac) as client:
        print(f"connected to {mac}")
        t0 = time.time()
        replies = await _send_and_listen(client, write_uuid, notify_uuid, cmd)
        print(f"{len(replies)} notification(s) in {time.time() - t0:.1f}s")


async def cmd(dev: str, cmd_hex: str, data_hex: str = ""):
    """Send one AA55 frame and decode the reply."""
    mac = await resolve(dev)
    frame = aa55(int(cmd_hex, 16), bytes.fromhex(data_hex.replace(" ", "")))
    async with BleakClient(mac) as client:
        print(f"connected to {mac}")
        replies = await _send_and_listen(client, PIPE_TX, PIPE_RX, frame)
        for r in replies:
            p = parse_aa55(r)
            if p:
                c, d, ok = p
                print(f"    cmd=0x{c:02X} len={len(d)} xor={'ok' if ok else 'BAD'} data={hexdump(d)}")


async def sweep(dev: str, start_hex: str = "00", end_hex: str = "FF", wait: str = "0.6"):
    """Send AA 55 <cmd> 00 <xor> for every cmd in range; report non-error replies."""
    mac = await resolve(dev)
    lo, hi, wait_s = int(start_hex, 16), int(end_hex, 16), float(wait)
    got: dict[int, list[bytes]] = {}

    async with BleakClient(mac) as client:
        print(f"connected to {mac}; sweeping 0x{lo:02X}..0x{hi:02X}")
        cur = [0]

        def on_notify(_, data: bytearray):
            got.setdefault(cur[0], []).append(bytes(data))

        await client.start_notify(PIPE_RX, on_notify)
        for c in range(lo, hi + 1):
            cur[0] = c
            await client.write_gatt_char(PIPE_TX, aa55(c), response=False)
            await asyncio.sleep(wait_s)
            for r in got.get(c, []):
                p = parse_aa55(r)
                tag = ""
                if p:
                    rc, d, ok = p
                    # 1-byte payload on echoed cmd looks like the NAK we saw (05)
                    tag = f"  [cmd=0x{rc:02X} len={len(d)} xor={'ok' if ok else 'BAD'}]"
                    if len(d) == 1 and rc == c:
                        tag += f"  NAK code {d[0]:02X}"
                    else:
                        tag += "  <== DATA"
                print(f"0x{c:02X}: ({len(r):3}) {hexdump(r)}{tag}")
        await client.stop_notify(PIPE_RX)

    interesting = [c for c, rs in got.items()
                   if any((p := parse_aa55(r)) and not (len(p[1]) == 1 and p[0] == c) for r in rs)]
    print("commands returning data:", " ".join(f"0x{c:02X}" for c in interesting) or "none")


def framing_variants(c: int) -> list[tuple[str, bytes]]:
    """Same command byte, different framing/checksum guesses."""
    body = bytes([c, 0x00])
    sum8 = lambda b: sum(b) & 0xFF  # noqa: E731
    out = []
    for hdr_name, hdr in (("AA55", b"\xaa\x55"), ("55AA", b"\x55\xaa")):
        f = hdr + body
        out += [
            (f"{hdr_name} xor-all", f + bytes([xor(f)])),
            (f"{hdr_name} xor-body", f + bytes([xor(body)])),
            (f"{hdr_name} sum-all", f + bytes([sum8(f)])),
            (f"{hdr_name} sum-body", f + bytes([sum8(body)])),
            (f"{hdr_name} ~sum-all", f + bytes([~sum8(f) & 0xFF])),
            (f"{hdr_name} ~sum-body", f + bytes([~sum8(body) & 0xFF])),
            (f"{hdr_name} sum16-body BE", f + sum(body).to_bytes(2, "big")),
            (f"{hdr_name} sum16-body LE", f + sum(body).to_bytes(2, "little")),
            (f"{hdr_name} no-chk", f),
            (f"{hdr_name} xor-all +0D0A", f + bytes([xor(f)]) + b"\r\n"),
            (f"{hdr_name} len=1 (counts chk)", hdr + bytes([c, 0x01]) + bytes([xor(hdr + bytes([c, 0x01]))])),
            (f"{hdr_name} addr byte first", hdr + b"\x01" + body + bytes([xor(hdr + b"\x01" + body)])),
        ]
    return out


async def variants(dev: str, cmd_hex: str = "90", wait: str = "0.6"):
    mac = await resolve(dev)
    c, wait_s = int(cmd_hex, 16), float(wait)
    async with BleakClient(mac) as client:
        print(f"connected to {mac}; trying framings for cmd 0x{c:02X}")
        got: list[bytes] = []
        client_notify = lambda _, d: got.append(bytes(d))  # noqa: E731
        await client.start_notify(PIPE_RX, client_notify)
        for name, frame in framing_variants(c):
            got.clear()
            await client.write_gatt_char(PIPE_TX, frame, response=False)
            await asyncio.sleep(wait_s)
            reply = " | ".join(hexdump(r) for r in got) or "(no reply)"
            print(f"{name:28} -> {hexdump(frame):40}  <- {reply}")
        await client.stop_notify(PIPE_RX)


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 1
    cmd, args = argv[1], argv[2:]
    try:
        if cmd == "scan":
            asyncio.run(scan())
        elif cmd == "dump":
            asyncio.run(dump(args[0] if args else DEVICE_MAC))
        elif cmd == "probe":
            asyncio.run(probe(args[0] if args else DEVICE_MAC))
        elif cmd == "raw":
            if len(args) == 3:
                args = [DEVICE_MAC, *args]
            asyncio.run(raw(*args[:4]))
        elif cmd == "cmd":
            asyncio.run(globals()["cmd"](DEVICE_MAC, *args[:2]))
        elif cmd == "sweep":
            asyncio.run(sweep(DEVICE_MAC, *args[:3]))
        elif cmd == "variants":
            asyncio.run(variants(DEVICE_MAC, *args[:2]))
        else:
            print(__doc__)
            return 1
    except (IndexError, TypeError):
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
