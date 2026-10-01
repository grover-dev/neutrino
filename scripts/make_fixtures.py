#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Generate tests/fixtures/bms_exchanges.json from the captured BMS traffic.

Every exchange is tagged with its provenance:
  captured  exact bytes seen on the wire, including how they were fragmented
  derived   frame rebuilt from register values that WERE read from the device,
            but whose exact wire bytes/fragmentation were not recorded
  synthetic constructed to exercise a code path (bad CRC, etc.); not from device

Run:  uv run scripts/make_fixtures.py
"""

import json
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "bms_exchanges.json"

SLAVE, FUNC = 0x01, 0x03


def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def hx(b: bytes) -> str:
    return b.hex()


def request(addr: int, count: int) -> bytes:
    f = bytes([SLAVE, FUNC]) + addr.to_bytes(2, "big") + count.to_bytes(2, "big")
    return f + crc16(f).to_bytes(2, "little")


def response(regs: list[int]) -> bytes:
    body = b"".join(r.to_bytes(2, "big") for r in regs)
    f = bytes([SLAVE, FUNC, len(body)]) + body
    return f + crc16(f).to_bytes(2, "little")


def exception(code: int) -> bytes:
    f = bytes([SLAVE, FUNC | 0x80, code])
    return f + crc16(f).to_bytes(2, "little")


def frag20(b: bytes) -> list[str]:
    """The bridge flushes in ~20 byte chunks; this is the observed pattern."""
    return [hx(b[i:i + 20]) for i in range(0, len(b), 20)]


# --- captured: exact response bytes observed on the wire -------------------
# (name, addr, count, response hex as captured, fragment sizes as observed)
CAPTURED = [
    ("status_8",   0x0000, 0x0008,
     "0103100000052500300064 13D5290F271000 01AB66", [20, 1]),
    ("status_6",   0x0000, 0x0006,
     "01030C0000052500300064 13D5290F 2ED2", None),
    ("alarm",      0x0009, 0x0001, "0103020000B844", None),
    ("protection", 0x000A, 0x0001, "0103020000B844", None),
    ("flags",      0x000B, 0x0001, "0103020C00BD44", None),
    ("cells_7",    0x000F, 0x0007,
     "01030E00040CDD0CDF0CE00CDA00000000C660", None),
    ("temps_11",   0x002E, 0x000B,
     "0103160001012000000000000000000000000000000000000006A4", [20, 7]),
    ("regs_003F",  0x003F, 0x0006,
     "01030C00000000C000C00000000000 8E20", None),
    ("devinfo_a",  0x00AA, 0x0005, "01030A503453313030412D3530F259", None),
    ("devinfo_b",  0x00AF, 0x0005, "01030A3536352D312E353000009635", None),
]

# --- derived: register values really read, wire bytes reconstructed --------
DERIVED = [
    ("aux_temps", 0x0039, [291, 0xFFFF]),          # mosfet 29.1 C, aux absent
    ("temp_slots_unused", 0x0030, [0] * 8),        # slots after the one sensor
    ("reg_004F", 0x004F, [1]),                     # meaning unknown
    ("devinfo_serial", 0x00B4, [0x4237, 0x3532, 0x3236, 0x3034, 0x3136,
                                0x3031, 0x3032, 0x2020, 0x2020, 0x2020]),
    ("devinfo_model_blank", 0x00BE, [0x2020] * 10),  # model slot, space-filled
    ("filler_block", 0x00CD, [0xC000] * 5),          # unimplemented registers
]


def build() -> dict:
    exchanges = []

    for name, addr, count, resp_hex, frags in CAPTURED:
        resp = bytes.fromhex(resp_hex.replace(" ", ""))
        if crc16(resp[:-2]) != int.from_bytes(resp[-2:], "little"):
            sys.exit(f"captured frame {name} fails CRC - transcription error")
        if len(resp) != 3 + resp[2] + 2:
            sys.exit(f"captured frame {name} has inconsistent length")
        regs = [int.from_bytes(resp[3 + i:5 + i], "big") for i in range(0, resp[2], 2)]
        if len(regs) != count:
            sys.exit(f"captured frame {name}: {len(regs)} regs, requested {count}")
        e = {
            "name": name, "source": "captured",
            "addr": addr, "count": count,
            "request": hx(request(addr, count)),
            "response": hx(resp),
            "registers": regs,
        }
        if frags:
            out, off = [], 0
            for n in frags:
                out.append(hx(resp[off:off + n]))
                off += n
            if off != len(resp):
                sys.exit(f"{name}: fragment sizes {frags} != frame length {len(resp)}")
            e["fragments"] = out
            e["fragments_observed"] = True
        exchanges.append(e)

    for name, addr, regs in DERIVED:
        resp = response(regs)
        exchanges.append({
            "name": name, "source": "derived",
            "addr": addr, "count": len(regs),
            "request": hx(request(addr, len(regs))),
            "response": hx(resp),
            "registers": regs,
            "fragments": frag20(resp),
        })

    # Sparse-map behaviour: 0x003A is readable but 0x003B is not, so a request
    # spanning it fails wholesale. Observed as an exception; code 0x03 seen.
    exchanges.append({
        "name": "hole_exception", "source": "derived",
        "addr": 0x003A, "count": 5,
        "request": hx(request(0x003A, 5)),
        "response": hx(exception(0x03)),
        "exception_code": 3,
        "comment": "spans nonexistent 0x003B; whole request is rejected",
    })

    # Negative case for the CRC check: last byte of a good frame flipped.
    good = bytes.fromhex("0103020C00BD44")
    exchanges.append({
        "name": "bad_crc", "source": "synthetic",
        "addr": 0x000B, "count": 1,
        "request": hx(request(0x000B, 1)),
        "response": hx(good[:-1] + bytes([good[-1] ^ 0xFF])),
        "expect_reject": "crc",
        "comment": "decoder must reject and not resync into garbage",
    })

    return {
        "device": {
            "ble_name": "B7522604160102",
            "mac": "53:EE:10:0C:35:37",
            "firmware": "P4S100A-50565-1.50",
            "serial": "B7522604160102",
            "chemistry": "LFP", "cells": 4, "nominal_v": 12,
            "design_capacity_ah": 100.0,
        },
        "transport": {
            "service": "00002760-08c2-11e1-9073-0e8ac72e1001",
            "tx_write_without_response": "00002760-08c2-11e1-9073-0e8ac72e0001",
            "rx_notify": "00002760-08c2-11e1-9073-0e8ac72e0002",
            "att_mtu_accepted": 517,
            "note": "Fragmentation is the UART bridge flushing ~20B, not the MTU. "
                    "A 21B reply still arrives as 20+1. Reassembly is mandatory.",
        },
        "framing": {
            "request": "[0x01,0x03,addr_be:u16,count_be:u16,crc_le:u16]",
            "response": "[0x01,0x03,byte_count:u8,data[byte_count],crc_le:u16]",
            "exception": "[0x01,0x83,code:u8,crc_le:u16]",
            "crc": "CRC-16/Modbus, init 0xFFFF, poly 0xA001, reflected, little-endian on wire",
            "reassembly": "buffer until 3 + buf[2] + 2 bytes; resync by dropping "
                          "until buf[0]==0x01 && buf[1] in (0x03,0x83)",
        },
        # Split patterns a reassembler should survive for any frame above.
        "fragment_patterns": {
            "observed_bridge": "20-byte chunks, remainder last (see fragments fields)",
            "single": "whole frame in one notification",
            "byte_at_a_time": "1 byte per notification",
            "header_split": "3 bytes, then the remainder",
            "crc_split": "all but last byte, then last byte",
        },
        "exchanges": exchanges,
        # Decoded snapshot of this pack at rest, as the vendor app also showed it.
        "expected_decode": {
            "from": "status_8 + cells_7 + temps_11 + aux_temps",
            "voltage_v": 13.17, "current_a": 0.0, "power_w": 0.0,
            "soc_pct": 48, "soh_pct": 100,
            "remaining_ah": 50.77, "full_capacity_ah": 105.11,
            "design_capacity_ah": 100.0, "cycles": 1,
            "alarm": 0, "protection": 0, "fault_code": 0,
            "status_flags": 0x0C, "charge_mosfet": True, "discharge_mosfet": True,
            "cell_count": 4,
            "cell_voltages_v": [3.293, 3.295, 3.296, 3.290],
            "cell_min_v": 3.290, "cell_max_v": 3.296, "cell_delta_mv": 6.0,
            "temps_c": [28.8], "mosfet_temp_c": 29.1,
            "aux_temp_c": None,
        },
        "sentinels": {"0xC000": "readable but unimplemented", "0xFFFF": "no sensor fitted"},
    }


def main() -> int:
    data = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, indent=2) + "\n")
    n = len(data["exchanges"])
    by = {}
    for e in data["exchanges"]:
        by[e["source"]] = by.get(e["source"], 0) + 1
    print(f"wrote {OUT.relative_to(OUT.parents[2])}  ({n} exchanges: "
          + ", ".join(f"{k} {v}" for k, v in sorted(by.items())) + ")")
    return 0


if __name__ == "__main__":
    sys.exit(main())
