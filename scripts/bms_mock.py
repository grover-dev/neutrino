#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Serve the recorded BMS register map over a pseudo-terminal.

Gives a Rust (or any) client a real byte-stream endpoint that answers Modbus
exactly as the battery did -- same register values, same ~20-byte fragmentation,
same reply latency -- with no Bluetooth and no hardware.

    uv run scripts/bms_mock.py
    # prints e.g.  PTY ready: /dev/pts/7

Point the client at that path. To get a stable path, symlink it or use socat:
    socat -d -d PTY,link=/tmp/bms,raw EXEC:'uv run scripts/bms_mock.py --stdio'

Options:
    --fragment {bridge,single,bytes,header,crc}   how to split replies
    --latency MS                                 reply delay (default 190, observed)
    --stdio                                      talk on stdin/stdout instead of a PTY
"""

import argparse
import json
import os
import pty
import sys
import termios
import time
import tty
from pathlib import Path

FIXTURE = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "bms_exchanges.json"
SLAVE, FUNC = 0x01, 0x03
EXC_ILLEGAL_ADDR = 0x03  # what the real pack returns for a range spanning a hole


def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def with_crc(frame: bytes) -> bytes:
    return frame + crc16(frame).to_bytes(2, "little")


class Model:
    """Register values and exact recorded frames, loaded from the fixture."""

    def __init__(self, path: Path):
        data = json.loads(path.read_text())
        self.regs: dict[int, int] = {}
        self.exact: dict[tuple[int, int], bytes] = {}
        self.holes: set[int] = set()
        for e in data["exchanges"]:
            if e.get("expect_reject"):
                continue
            if "exception_code" in e:
                # The request spanned a nonexistent register; mark the ones not
                # otherwise known so any range crossing them also fails.
                self.holes.update(range(e["addr"], e["addr"] + e["count"]))
                continue
            for i, v in enumerate(e["registers"]):
                self.regs[e["addr"] + i] = v
            self.exact[(e["addr"], e["count"])] = bytes.fromhex(e["response"])
        self.holes -= set(self.regs)  # registers we later learned do exist

    def reply(self, addr: int, count: int) -> bytes:
        if (hit := self.exact.get((addr, count))) is not None:
            return hit  # byte-identical to what the device sent
        wanted = range(addr, addr + count)
        if any(a in self.holes or a not in self.regs for a in wanted):
            return with_crc(bytes([SLAVE, FUNC | 0x80, EXC_ILLEGAL_ADDR]))
        body = b"".join(self.regs[a].to_bytes(2, "big") for a in wanted)
        return with_crc(bytes([SLAVE, FUNC, len(body)]) + body)


def split(frame: bytes, how: str) -> list[bytes]:
    if how == "single":
        return [frame]
    if how == "bytes":
        return [frame[i:i + 1] for i in range(len(frame))]
    if how == "header":
        return [frame[:3], frame[3:]]
    if how == "crc":
        return [frame[:-1], frame[-1:]]
    return [frame[i:i + 20] for i in range(0, len(frame), 20)]  # bridge


def serve(read_fd: int, write_fd: int, model: Model, args) -> None:
    buf = bytearray()
    while True:
        try:
            chunk = os.read(read_fd, 256)
        except OSError:
            return
        if not chunk:
            return
        buf.extend(chunk)
        while len(buf) >= 8:
            if buf[0] != SLAVE or buf[1] != FUNC:
                buf.pop(0)
                continue
            req, buf = bytes(buf[:8]), buf[8:]
            if crc16(req[:-2]) != int.from_bytes(req[-2:], "little"):
                print(f"  <- bad CRC, ignoring: {req.hex(' ')}", file=sys.stderr)
                continue
            addr = int.from_bytes(req[2:4], "big")
            count = int.from_bytes(req[4:6], "big")
            frame = model.reply(addr, count)
            kind = "EXC" if frame[1] & 0x80 else "ok "
            print(f"  0x{addr:04X} x{count:<3} -> {kind} {len(frame):2}B", file=sys.stderr)
            time.sleep(args.latency / 1000)
            for part in split(frame, args.fragment):
                os.write(write_fd, part)
                time.sleep(0.002)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--fragment", default="bridge",
                   choices=["bridge", "single", "bytes", "header", "crc"])
    p.add_argument("--latency", type=float, default=190.0, metavar="MS")
    p.add_argument("--stdio", action="store_true")
    p.add_argument("--fixture", type=Path, default=FIXTURE)
    args = p.parse_args()

    model = Model(args.fixture)
    print(f"loaded {len(model.regs)} registers, {len(model.exact)} exact frames, "
          f"{len(model.holes)} holes; fragment={args.fragment}", file=sys.stderr)

    if args.stdio:
        serve(sys.stdin.fileno(), sys.stdout.fileno(), model, args)
        return 0

    master, slave = pty.openpty()
    tty.setraw(master)
    tty.setraw(slave)
    termios.tcsetattr(slave, termios.TCSANOW, termios.tcgetattr(slave))
    print(f"PTY ready: {os.ttyname(slave)}", flush=True)
    try:
        serve(master, master, model, args)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
