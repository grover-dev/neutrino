#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["bleak>=0.22"]
# ///
"""Read telemetry from a Dyness 12V LFP battery over BLE, without the vendor app.

The BLE module (Nations NS-BLE-1.0) is a transparent serial bridge to the BMS,
which speaks plain Modbus RTU (slave 0x01, function 0x03). Register map below was
recovered by relaying the vendor app through scripts/bms_mitm.py and decoding the
capture with scripts/parse_snoop.py.

Usage:
    uv run scripts/bms_poll.py                  # one reading, human readable
    uv run scripts/bms_poll.py --json           # one reading as JSON
    uv run scripts/bms_poll.py --watch 10       # poll every 10s
    uv run scripts/bms_poll.py --watch 10 --csv telemetry.csv

Note: the battery accepts only one BLE connection, so close the vendor app first.

Register map (Modbus holding registers, function 0x03). Entries marked * were
confirmed against values the vendor app displayed for the same pack:

    0x0000  current, signed, /100 A  *        0x000F  cell count *
    0x0001  voltage, /100 V *                 0x0010+ cell voltages, mV *
    0x0002  SOC, % *                          0x002E  temp sensor count *
    0x0003  SOH, %                            0x002F-0x0038  temps, /10 C *
    0x0004  remaining, /100 Ah *              0x0039  MOSFET temp, /10 C
    0x0005  full capacity, /100 Ah            0x003A  aux temp (0xFFFF = absent)
    0x0006  design capacity, /100 Ah          0x00AA  version, 18 bytes ASCII *
    0x0007  cycle count                       0x00B4  serial, space-padded *
    0x0009  alarm *
    0x000A  protection *
    0x000B  low byte fault code *, high byte status flags
            (0x04 charge MOSFET *, 0x08 discharge MOSFET *)

The map is sparse: 0x003B returns Modbus exception 0x03, and many registers read
0xC000 filler. Unresolved: the lifetime charge/discharge counters (0x003F/0x0040
and 0x0043/0x0044 are the likely 32-bit pair but read zero on this pack), the
ACin and heater bits in 0x000B (never set in any capture), the meaning of 0x004F
(reads 1), and the model string, which is absent from 0x00AA+60 entirely.
"""

import argparse
import asyncio
import csv
import json
import struct
import sys
import time
from datetime import datetime, timezone

from bleak import BleakClient, BleakScanner

DEVICE = "53:EE:10:0C:35:37"  # MAC, or an advertised name like "B7522604160102"

PIPE_TX = "00002760-08c2-11e1-9073-0e8ac72e0001"  # write commands here
PIPE_RX = "00002760-08c2-11e1-9073-0e8ac72e0002"  # replies arrive here

SLAVE = 0x01
FUNC_READ = 0x03
MAX_CELLS = 16
MAX_TEMPS = 10
REPLY_TIMEOUT_S = 3.0

# Readable-but-unimplemented registers come back as this filler, so never decode
# it as a value. 0x0041/0x0042 read 0xC000, which is why they are not the
# lifetime counters they first looked like.
FILLER = 0xC000

# A temperature slot with no sensor fitted reads 0xFFFF (-0.1 after scaling);
# 0x003A reads this on a pack that has only the one MOSFET sensor at 0x0039.
ABSENT = 0xFFFF


def crc16_modbus(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def build_read(addr: int, count: int) -> bytes:
    frame = struct.pack(">BBHH", SLAVE, FUNC_READ, addr, count)
    return frame + struct.pack("<H", crc16_modbus(frame))


class Bms:
    """Modbus RTU over the BLE serial pipe."""

    def __init__(self, client: BleakClient):
        self.client = client
        self._buf = bytearray()
        self._frame: asyncio.Future | None = None

    def _on_notify(self, _sender, data: bytearray):
        self._buf.extend(data)
        # A complete reply is: slave, func, byte_count, <byte_count bytes>, crc16
        while len(self._buf) >= 3:
            if self._buf[0] != SLAVE or self._buf[1] not in (FUNC_READ, FUNC_READ | 0x80):
                self._buf.pop(0)  # resync
                continue
            if self._buf[1] & 0x80:  # Modbus exception
                if len(self._buf) < 5:
                    return
                frame, self._buf = bytes(self._buf[:5]), self._buf[5:]
            else:
                total = 3 + self._buf[2] + 2
                if len(self._buf) < total:
                    return
                frame, self._buf = bytes(self._buf[:total]), self._buf[total:]
            if crc16_modbus(frame[:-2]) != int.from_bytes(frame[-2:], "little"):
                continue  # bad CRC, drop it
            if self._frame and not self._frame.done():
                self._frame.set_result(frame)

    async def read_regs_chunked(self, addr: int, count: int, chunk: int = 5) -> list[int]:
        """Read a long register range in small requests.

        The device-info block is fetched by the vendor app in 5-register chunks;
        there is no evidence the firmware accepts a single large read, so don't.
        """
        out: list[int] = []
        for off in range(0, count, chunk):
            out.extend(await self.read_regs(addr + off, min(chunk, count - off)))
        return out

    async def read_regs_sparse(self, addr: int, count: int,
                               chunk: int = 5) -> list[int | None]:
        """Read a range that may contain invalid registers; None where unreadable.

        The map is sparse (0x003A-0x003E returns Modbus exception 0x03), and one
        bad register fails the whole request, so fall back to single reads to work
        out exactly which registers in a failing chunk exist.
        """
        out: list[int | None] = [None] * count
        for off in range(0, count, chunk):
            n = min(chunk, count - off)
            try:
                out[off:off + n] = await self.read_regs(addr + off, n)
                continue
            except (TimeoutError, OSError):
                pass
            if n == 1:
                continue
            for i in range(n):
                try:
                    out[off + i] = (await self.read_regs(addr + off + i, 1))[0]
                except (TimeoutError, OSError):
                    pass
        return out

    async def read_regs(self, addr: int, count: int) -> list[int]:
        """Read `count` 16-bit holding registers starting at `addr`."""
        loop = asyncio.get_running_loop()
        self._frame = loop.create_future()
        self._buf.clear()
        await self.client.write_gatt_char(PIPE_TX, build_read(addr, count), response=False)
        try:
            frame = await asyncio.wait_for(self._frame, REPLY_TIMEOUT_S)
        except asyncio.TimeoutError:
            raise TimeoutError(f"no reply for regs 0x{addr:04X}+{count}") from None
        if frame[1] & 0x80:
            raise OSError(f"modbus exception 0x{frame[2]:02X} for regs 0x{addr:04X}")
        payload = frame[3:3 + frame[2]]
        return [int.from_bytes(payload[i:i + 2], "big") for i in range(0, len(payload), 2)]


def s16(v: int) -> int:
    return v - 0x10000 if v & 0x8000 else v


def regs_to_ascii(regs: list[int]) -> bytes:
    return b"".join(v.to_bytes(2, "big") for v in regs)


def clean_str(raw: bytes) -> str:
    return raw.split(b"\x00")[0].decode("ascii", "replace").strip()


def ascii_runs(raw: bytes, min_len: int = 3) -> list[tuple[int, str]]:
    """(offset, text) for each run of printable ASCII, longest-first scan order."""
    runs, start = [], None
    for i, b in enumerate(raw + b"\x00"):
        if 32 <= b < 127:
            start = i if start is None else start
        elif start is not None:
            if i - start >= min_len:
                runs.append((start, raw[start:i].decode("ascii").strip()))
            start = None
    return runs


async def read_device_info(bms: Bms) -> dict:
    """Device-info strings from 0x00AA.

    Confirmed by dumping 0x00AA+60 on the device: version at 0x00AA (18 bytes),
    serial at 0x00B4 space-padded through 0x00C7, then zeros, then 0xC000 filler
    from 0x00CD. There is NO model string in this block -- the app's "AR1.2-MINI
    BT" comes from somewhere else (or is app-side), so don't invent an offset for
    it. Still scan for a third run in case other firmware places one here.
    """
    raw = regs_to_ascii(await bms.read_regs_chunked(0x00AA, 30))
    info = {
        "version": clean_str(raw[0:18]),
        "serial_number": clean_str(raw[20:40]),
    }
    rest = [t for _, t in ascii_runs(raw[40:]) if t]
    if rest:
        info["model"] = rest[0]
    return info


async def read_all(bms: Bms) -> dict:
    r = await bms.read_regs(0x0000, 0x000C)  # status block, through the flags word
    out = {
        "current_a": s16(r[0]) / 100,
        "voltage_v": r[1] / 100,
        "soc_pct": r[2],
        "soh_pct": r[3],
        "remaining_ah": r[4] / 100,
        "full_capacity_ah": r[5] / 100,
        "design_capacity_ah": r[6] / 100,
        "cycles": r[7],
        "alarm": r[9],
        "protection": r[10],
        # 0x000B is split: low byte is the fault code the app labels "Faulty"
        # (0x00 = none), high byte carries the status flags.
        "fault_code": r[11] & 0xFF,
        "status_flags": (r[11] >> 8) & 0xFF,
        "charge_mosfet": bool(r[11] & 0x0400),
        "discharge_mosfet": bool(r[11] & 0x0800),
        # Only the two MOSFET bits (high byte 0x04 / 0x08) are confirmed. The app
        # also shows "ACin" and "heater", but both read off in every capture so
        # far, so their bits are never set and their positions stay unknown.
        # This lists which flag bits are actually high, so a new one can be
        # identified by re-reading with a charger attached or the heater running.
        "status_bits_set": [i for i in range(8) if (r[11] >> 8) & (1 << i)],
    }
    out["power_w"] = round(out["voltage_v"] * out["current_a"], 2)

    # Ask for the cell count first, then exactly that many registers: a large
    # blanket read is not something the BMS was observed to accept.
    n = min((await bms.read_regs(0x000F, 1))[0], MAX_CELLS)
    out["cell_count"] = n
    cells = await bms.read_regs(0x0010, n) if n else []
    out["cell_voltages_v"] = [v / 1000 for v in cells]
    if n:
        lo, hi = min(out["cell_voltages_v"]), max(out["cell_voltages_v"])
        out["cell_min_v"], out["cell_max_v"] = lo, hi
        out["cell_delta_mv"] = round((hi - lo) * 1000, 1)

    # 0x002E is the sensor count, 0x002F-0x0038 the 10 slots (unused ones read 0).
    temps = await bms.read_regs(0x002E, 1 + MAX_TEMPS)
    tn = min(temps[0], MAX_TEMPS)
    out["temps_c"] = [s16(t) / 10 for t in temps[1:1 + tn] if t != ABSENT]

    # Aux temperatures sit just past the 10-slot array (0x002F-0x0038): 0x0039 is
    # the MOSFET sensor, 0x003A a second slot that reads ABSENT when unfitted.
    try:
        aux = await bms.read_regs(0x0039, 2)
    except (TimeoutError, OSError):
        aux = []
    for name, v in zip(("mosfet_temp_c", "aux_temp_c"), aux):
        if v not in (ABSENT, FILLER):
            out[name] = s16(v) / 10

    # Lifetime charge/discharge counters: the app shows both and polls 0x003F+6,
    # which reads 0000 0000 C000 C000 0000 0000. The 0xC000 pair is unimplemented
    # filler, so the plausible layout is a 32-bit counter at 0x003F/0x0040 and
    # another at 0x0043/0x0044 -- but both read zero on this pack, so that cannot
    # be confirmed yet. Report the raw registers rather than a decoded number;
    # cycle a few Ah through the battery and re-read to see which ones move.
    try:
        out["regs_003F"] = await bms.read_regs(0x003F, 6)
    except (TimeoutError, OSError):
        pass

    return out


def render(d: dict) -> str:
    lines = [
        f"  {d['voltage_v']:.2f} V   {d['current_a']:+.2f} A   {d['power_w']:+.1f} W",
        f"  SOC {d['soc_pct']}%   SOH {d['soh_pct']}%   "
        f"{d['remaining_ah']:.2f} / {d['full_capacity_ah']:.2f} Ah   {d['cycles']} cycles",
    ]
    if d.get("cell_voltages_v"):
        cells = "  ".join(f"{v:.3f}" for v in d["cell_voltages_v"])
        lines.append(f"  cells ({d['cell_count']}): {cells}   Δ {d['cell_delta_mv']:.0f} mV")
    if d.get("temps_c") or "mosfet_temp_c" in d:
        temps = ", ".join(f"{t:.1f} °C" for t in d.get("temps_c", []))
        for name, label in (("mosfet_temp_c", "mosfet"), ("aux_temp_c", "aux")):
            if name in d:
                temps += f"   {label} {d[name]:.1f} °C"
        lines.append(f"  temps: {temps}")
    lines.append(
        f"  charge FET {'on' if d['charge_mosfet'] else 'off'}   "
        f"discharge FET {'on' if d['discharge_mosfet'] else 'off'}   "
        f"flags 0x{d['status_flags']:02X}"
    )
    lines.append(
        f"  alarm 0x{d['alarm']:04X}   protection 0x{d['protection']:04X}   "
        f"fault 0x{d['fault_code']:02X}"
    )
    if d.get("regs_003F"):
        lines.append("  regs 0x003F: " + " ".join(f"{v:04X}" for v in d["regs_003F"]))
    return "\n".join(lines)


async def resolve(dev: str) -> str:
    if ":" in dev:
        return dev
    found = await BleakScanner.find_device_by_name(dev, timeout=15.0)
    if found is None:
        sys.exit(f"device {dev!r} not found")
    return found.address


async def dump_raw(bms: Bms, addr: int, count: int) -> None:
    """Print a register range as hex, decimal and ASCII, to map unknown fields.

    '----' marks a register that does not exist, 'fill' one returning the 0xC000
    unimplemented filler; neither is a value.
    """
    regs = await bms.read_regs_sparse(addr, count)

    def cell(v: int | None) -> str:
        if v is None:
            return "----"
        return "fill" if v == FILLER else f"{v:04X}"

    for i in range(0, len(regs), 8):
        chunk = regs[i:i + 8]
        hexs = " ".join(cell(v) for v in chunk)
        txt = "".join(
            chr(b) if 32 <= b < 127 else "."
            for v in chunk
            for b in (v.to_bytes(2, "big") if v is not None and v != FILLER else b"..")
        )
        print(f"  0x{addr + i:04X}: {hexs:<39}  |{txt}|")

    real = [(addr + i, v) for i, v in enumerate(regs)
            if v is not None and v != FILLER]
    print("\n  real registers:", ", ".join(f"0x{a:04X}={v}" for a, v in real) or "none")
    missing = [addr + i for i, v in enumerate(regs) if v is None]
    if missing:
        print("  nonexistent:", ", ".join(f"0x{a:04X}" for a in missing))

    raw = regs_to_ascii([v if v not in (None, FILLER) else 0 for v in regs])
    runs = ascii_runs(raw)
    if runs:
        print("  ascii runs:")
        for off, t in runs:
            print(f"    0x{addr + off // 2:04X} (+{off}B): {t!r}")


async def main(args) -> int:
    mac = await resolve(args.device)
    writer = None
    csv_file = None
    if args.csv:
        csv_file = open(args.csv, "a", newline="", buffering=1)  # noqa: SIM115

    async with BleakClient(mac) as client:
        bms = Bms(client)
        await client.start_notify(PIPE_RX, bms._on_notify)

        if args.raw:
            addr, count = args.raw
            print(f"registers 0x{addr:04X}..0x{addr + count - 1:04X}:")
            await dump_raw(bms, addr, count)
            return 0

        info = None
        if not args.no_info:
            try:
                info = await read_device_info(bms)
            except (TimeoutError, OSError) as e:
                print(f"device info unavailable: {e}", file=sys.stderr)
        if info:
            if args.json:
                print(json.dumps({"type": "device_info", **info}))
            else:
                parts = [info[k] for k in ("model", "serial_number", "version")
                         if info.get(k)]
                print("  ".join(parts))

        while True:
            try:
                data = await read_all(bms)
            except (TimeoutError, OSError) as e:
                print(f"read failed: {e}", file=sys.stderr)
                if not args.watch:
                    return 1
                await asyncio.sleep(args.watch)
                continue

            data["timestamp"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            if args.json:
                print(json.dumps(data))
            else:
                print(f"{data['timestamp']}")
                print(render(data))
            if csv_file is not None:
                flat = {k: (";".join(map(str, v)) if isinstance(v, list) else v)
                        for k, v in data.items()}
                if writer is None:
                    writer = csv.DictWriter(csv_file, fieldnames=list(flat))
                    if csv_file.tell() == 0:
                        writer.writeheader()
                writer.writerow(flat)

            if not args.watch:
                return 0
            await asyncio.sleep(args.watch)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--device", default=DEVICE, help="MAC address or advertised name")
    p.add_argument("--watch", type=float, metavar="SECONDS",
                   help="poll continuously every SECONDS")
    p.add_argument("--json", action="store_true", help="emit JSON lines")
    p.add_argument("--csv", metavar="FILE", help="append readings to a CSV file")
    p.add_argument("--no-info", action="store_true",
                   help="skip the device-info read (model/serial/firmware)")
    p.add_argument("--raw", nargs=2, metavar=("ADDR", "COUNT"),
                   type=lambda x: int(x, 0),
                   help="dump a register range as hex/decimal/ASCII and exit, "
                        "e.g. --raw 0xAA 60")
    try:
        sys.exit(asyncio.run(main(p.parse_args())))
    except KeyboardInterrupt:
        pass
