#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["bleak>=0.22"]
# ///
"""Poll the BMS at 1 Hz and stream readings as JSON lines over a TCP socket.

    uv run scripts/bms_server.py              # listen on 127.0.0.1:9000

Clients receive one JSON object per line, one per second. The schema is flat and
fixed -- no arrays, no nulls -- so it maps to a plain struct:

    {"seq":1,"ts":"...","voltage_v":13.17,...,"cell_1_v":3.293,...,"temp_1_c":28.8}\n

Cell and temp-sensor counts are read once at startup (they don't change), so a
cycle is 4 Modbus reads at ~190 ms each -- about 760 ms, inside the 1 s budget.

Protocol details: see BMS_README.md.
"""

import argparse
import asyncio
import json
import struct
import sys
from datetime import datetime, timezone

from bleak import BleakClient, BleakScanner

DEVICE = "53:EE:10:0C:35:37"
PIPE_TX = "00002760-08c2-11e1-9073-0e8ac72e0001"
PIPE_RX = "00002760-08c2-11e1-9073-0e8ac72e0002"

SLAVE, FUNC = 0x01, 0x03
MAX_CELLS, MAX_TEMPS = 16, 10

# Readings are emitted as a flat, fixed schema: exactly this many cell and temp
# fields every time, zero-filled if the pack reports fewer. Sized for this 4S
# pack with one sensor; raise if you point it at a bigger one.
CELL_FIELDS, TEMP_FIELDS = 4, 1
ABSENT = 0xFFFF
REPLY_TIMEOUT_S = 3.0


def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def s16(v: int) -> int:
    return v - 0x10000 if v & 0x8000 else v


class Bms:
    def __init__(self, client: BleakClient):
        self.client = client
        self._buf = bytearray()
        self._frame: asyncio.Future | None = None

    def on_notify(self, _sender, data: bytearray) -> None:
        self._buf.extend(data)
        while len(self._buf) >= 3:
            if self._buf[0] != SLAVE or self._buf[1] not in (FUNC, FUNC | 0x80):
                self._buf.pop(0)
                continue
            if self._buf[1] & 0x80:
                if len(self._buf) < 5:
                    return
                frame, self._buf = bytes(self._buf[:5]), self._buf[5:]
            else:
                total = 3 + self._buf[2] + 2
                if len(self._buf) < total:
                    return
                frame, self._buf = bytes(self._buf[:total]), self._buf[total:]
            if crc16(frame[:-2]) != int.from_bytes(frame[-2:], "little"):
                continue
            if self._frame and not self._frame.done():
                self._frame.set_result(frame)

    async def read(self, addr: int, count: int) -> list[int]:
        self._frame = asyncio.get_running_loop().create_future()
        self._buf.clear()
        req = struct.pack(">BBHH", SLAVE, FUNC, addr, count)
        await self.client.write_gatt_char(
            PIPE_TX, req + struct.pack("<H", crc16(req)), response=False)
        try:
            frame = await asyncio.wait_for(self._frame, REPLY_TIMEOUT_S)
        except asyncio.TimeoutError:
            raise TimeoutError(f"no reply for 0x{addr:04X}+{count}") from None
        if frame[1] & 0x80:
            raise OSError(f"modbus exception 0x{frame[2]:02X} at 0x{addr:04X}")
        body = frame[3:3 + frame[2]]
        return [int.from_bytes(body[i:i + 2], "big") for i in range(0, len(body), 2)]


async def read_reading(bms: Bms, n_cells: int, n_temps: int) -> dict:
    r = await bms.read(0x0000, 12)
    cells = [v / 1000 for v in await bms.read(0x0010, n_cells)] if n_cells else []
    temps = [s16(v) / 10 for v in await bms.read(0x002F, n_temps)] if n_temps else []
    aux = await bms.read(0x0039, 2)

    voltage, current = r[1] / 100, s16(r[0]) / 100
    return {
        "voltage_v": voltage,
        "current_a": current,
        "power_w": round(voltage * current, 2),
        "soc_pct": r[2],
        "soh_pct": r[3],
        "remaining_ah": r[4] / 100,
        "full_capacity_ah": r[5] / 100,
        "design_capacity_ah": r[6] / 100,
        "cycles": r[7],
        "alarm": r[9],
        "protection": r[10],
        "fault_code": r[11] & 0xFF,
        "status_flags": (r[11] >> 8) & 0xFF,
        "charge_mosfet": bool(r[11] & 0x0400),
        "discharge_mosfet": bool(r[11] & 0x0800),
        **{f"cell_{i + 1}_v": cells[i] if i < len(cells) else 0.0
           for i in range(CELL_FIELDS)},
        "cell_min_v": min(cells) if cells else 0.0,
        "cell_max_v": max(cells) if cells else 0.0,
        "cell_delta_mv": round((max(cells) - min(cells)) * 1000, 1) if cells else 0.0,
        **{f"temp_{i + 1}_c": temps[i] if i < len(temps) else 0.0
           for i in range(TEMP_FIELDS)},
        # An unfitted sensor reads ABSENT; report 0.0 rather than null so every
        # field has one type. 0.0 therefore means "absent or actually 0 C".
        "mosfet_temp_c": s16(aux[0]) / 10 if aux[0] != ABSENT else 0.0,
        "aux_temp_c": s16(aux[1]) / 10 if aux[1] != ABSENT else 0.0,
    }


class Hub:
    """Fan out JSON lines to connected clients."""

    def __init__(self) -> None:
        self.clients: set[asyncio.StreamWriter] = set()

    async def on_connect(self, _reader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        self.clients.add(writer)
        print(f"client connected: {peer} ({len(self.clients)} total)", file=sys.stderr)
        try:
            await writer.wait_closed()
        finally:
            self.clients.discard(writer)
            print(f"client gone: {peer} ({len(self.clients)} left)", file=sys.stderr)

    def broadcast(self, obj: dict) -> None:
        line = (json.dumps(obj, separators=(",", ":")) + "\n").encode()
        for w in list(self.clients):
            if w.is_closing():
                self.clients.discard(w)
                continue
            try:
                w.write(line)
            except Exception:  # noqa: BLE001
                self.clients.discard(w)


async def resolve(dev: str) -> str:
    if ":" in dev:
        return dev
    found = await BleakScanner.find_device_by_name(dev, timeout=15.0)
    if found is None:
        sys.exit(f"device {dev!r} not found")
    return found.address


async def main(args) -> int:
    hub = Hub()
    server = await asyncio.start_server(hub.on_connect, args.host, args.port)
    print(f"listening on {args.host}:{args.port}", file=sys.stderr)

    mac = await resolve(args.device)
    async with BleakClient(mac) as client:
        bms = Bms(client)
        await client.start_notify(PIPE_RX, bms.on_notify)
        print(f"connected to {mac}", file=sys.stderr)

        # Counts are static; read once so each cycle is only 4 reads.
        n_cells = min((await bms.read(0x000F, 1))[0], MAX_CELLS)
        n_temps = min((await bms.read(0x002E, 1))[0], MAX_TEMPS)
        print(f"{n_cells} cells, {n_temps} temp sensors", file=sys.stderr)

        async with server:
            seq, loop = 0, asyncio.get_running_loop()
            next_t = loop.time()
            while True:
                try:
                    reading = await read_reading(bms, n_cells, n_temps)
                except (TimeoutError, OSError) as e:
                    print(f"read failed: {e}", file=sys.stderr)
                else:
                    seq += 1
                    hub.broadcast({
                        "seq": seq,
                        "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                        **reading,
                    })
                next_t += args.interval
                delay = next_t - loop.time()
                if delay < 0:
                    print(f"cycle overran by {-delay * 1000:.0f} ms", file=sys.stderr)
                    next_t = loop.time()
                else:
                    await asyncio.sleep(delay)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--device", default=DEVICE, help="MAC or advertised name")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=9000)
    p.add_argument("--interval", type=float, default=1.0, metavar="SECONDS")
    try:
        sys.exit(asyncio.run(main(p.parse_args())))
    except KeyboardInterrupt:
        pass
