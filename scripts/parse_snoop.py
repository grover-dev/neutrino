#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Parse a `btmon -w` capture into a clean per-connection ATT transcript.

`sudo btmon | grep` interleaves every link and both GATT directions, which makes
it useless for answering "what did the phone actually see?". This decodes the
btsnoop/monitor file properly and groups ATT traffic by connection handle.

Usage:
    sudo btmon -w /tmp/phone.snoop          # capture while the app connects
    uv run scripts/parse_snoop.py /tmp/phone.snoop
    uv run scripts/parse_snoop.py /tmp/phone.snoop --handle 3   # one link only

Directions are from the laptop's point of view:
    TX  laptop -> peer   (when it's a response, the laptop is the GATT server)
    RX  peer -> laptop
"""

import struct
import sys

# btsnoop monitor (datalink 2001) opcodes we care about
OP_ACL_TX = 0x04
OP_ACL_RX = 0x05

ATT_CID = 0x0004

ATT_NAMES = {
    0x01: "Error Response", 0x02: "Exchange MTU Req", 0x03: "Exchange MTU Rsp",
    0x04: "Find Info Req", 0x05: "Find Info Rsp",
    0x06: "Find By Type Value Req", 0x07: "Find By Type Value Rsp",
    0x08: "Read By Type Req", 0x09: "Read By Type Rsp",
    0x0A: "Read Req", 0x0B: "Read Rsp",
    0x0C: "Read Blob Req", 0x0D: "Read Blob Rsp",
    0x10: "Read By Group Type Req", 0x11: "Read By Group Type Rsp",
    0x12: "Write Req", 0x13: "Write Rsp", 0x52: "Write Command",
    0x1B: "Notification", 0x1D: "Indication", 0x1E: "Confirmation",
}

ATT_ERRORS = {
    0x01: "Invalid Handle", 0x02: "Read Not Permitted", 0x03: "Write Not Permitted",
    0x05: "Insufficient Authentication", 0x06: "Request Not Supported",
    0x0A: "Attribute Not Found", 0x0C: "Insufficient Encryption Key Size",
    0x0E: "Unlikely Error", 0x0F: "Insufficient Encryption",
    0x11: "Insufficient Resources",
}


def hexs(b: bytes) -> str:
    return " ".join(f"{x:02X}" for x in b)


def uuid_str(raw: bytes) -> str:
    """16-bit or 128-bit UUID, little-endian on the wire."""
    if len(raw) == 2:
        return f"0x{int.from_bytes(raw, 'little'):04X}"
    if len(raw) == 16:
        b = raw[::-1]
        h = b.hex()
        return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"
    return hexs(raw)


def read_records(path: str):
    with open(path, "rb") as f:
        hdr = f.read(16)
        if not hdr.startswith(b"btsnoop\x00"):
            sys.exit("not a btsnoop file (use `btmon -w <file>`)")
        datalink = struct.unpack(">I", hdr[12:16])[0]
        while True:
            rec = f.read(24)
            if len(rec) < 24:
                return
            olen, ilen, flags, _drops, ts = struct.unpack(">IIIIq", rec)
            data = f.read(ilen)
            if len(data) < ilen:
                return
            yield datalink, flags, ts, data


def decode_att(pdu: bytes) -> str:
    if not pdu:
        return "(empty)"
    op = pdu[0]
    name = ATT_NAMES.get(op, f"Unknown 0x{op:02X}")
    body = pdu[1:]

    if op == 0x01 and len(body) >= 4:
        err = body[3]
        return (f"{name}: on op 0x{body[0]:02X} handle 0x{int.from_bytes(body[1:3],'little'):04X}"
                f" -> {ATT_ERRORS.get(err, f'0x{err:02X}')}")

    if op in (0x10, 0x08) and len(body) >= 6:
        s, e = struct.unpack("<HH", body[0:4])
        return f"{name}: handles 0x{s:04X}-0x{e:04X} type {uuid_str(body[4:])}"

    if op == 0x11 and len(body) >= 1:  # Read By Group Type Rsp -> services
        ln = body[0]
        out = []
        for i in range(1, len(body) - ln + 1, ln):
            ent = body[i:i + ln]
            if len(ent) < 4:
                break
            s, e = struct.unpack("<HH", ent[0:4])
            out.append(f"0x{s:04X}-0x{e:04X} {uuid_str(ent[4:])}")
        return f"{name}: " + " | ".join(out)

    if op == 0x09 and len(body) >= 1:  # Read By Type Rsp -> characteristics
        ln = body[0]
        out = []
        for i in range(1, len(body) - ln + 1, ln):
            ent = body[i:i + ln]
            if len(ent) < 2:
                break
            h = int.from_bytes(ent[0:2], "little")
            val = ent[2:]
            if len(val) >= 3:  # characteristic declaration: props, value handle, uuid
                props, vh = val[0], int.from_bytes(val[1:3], "little")
                out.append(f"h0x{h:04X} props0x{props:02X} val_h0x{vh:04X} {uuid_str(val[3:])}")
            else:
                out.append(f"h0x{h:04X} {hexs(val)}")
        return f"{name}: " + " | ".join(out)

    if op == 0x05 and len(body) >= 1:  # Find Info Rsp -> descriptors
        fmt = body[0]
        size = 4 if fmt == 1 else 18
        out = []
        for i in range(1, len(body) - size + 2, size):
            ent = body[i:i + size]
            if len(ent) < 4:
                break
            h = int.from_bytes(ent[0:2], "little")
            out.append(f"h0x{h:04X} {uuid_str(ent[2:])}")
        return f"{name}: " + " | ".join(out)

    if op in (0x0A, 0x0C) and len(body) >= 2:
        return f"{name}: handle 0x{int.from_bytes(body[0:2],'little'):04X}"

    if op in (0x12, 0x52) and len(body) >= 2:
        h = int.from_bytes(body[0:2], "little")
        val = body[2:]
        return f"{name}: handle 0x{h:04X} value[{len(val)}] {hexs(val)}"

    if op in (0x1B, 0x1D) and len(body) >= 2:
        h = int.from_bytes(body[0:2], "little")
        val = body[2:]
        return f"{name}: handle 0x{h:04X} value[{len(val)}] {hexs(val)}"

    if op == 0x0B:
        return f"{name}: value[{len(body)}] {hexs(body)}" + (
            f"  ascii={body.decode('ascii', 'replace')!r}"
            if body and all(32 <= c < 127 or c == 0 for c in body) else "")

    if op in (0x02, 0x03) and len(body) >= 2:
        return f"{name}: MTU {int.from_bytes(body[0:2], 'little')}"

    return f"{name}: {hexs(body)}" if body else name


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 1
    path = argv[1]
    want = None
    if "--handle" in argv:
        want = int(argv[argv.index("--handle") + 1])

    t0 = None
    seen_handles = {}
    for datalink, flags, ts, data in read_records(path):
        opcode = flags & 0xFFFF
        if opcode not in (OP_ACL_TX, OP_ACL_RX) or len(data) < 8:
            continue
        hf, dlen = struct.unpack("<HH", data[0:4])
        handle = hf & 0x0FFF
        pb = (hf >> 12) & 0x3
        if pb == 0x01:  # continuation fragment; we only decode complete first frags
            continue
        l2len, cid = struct.unpack("<HH", data[4:8])
        if cid != ATT_CID:
            continue
        pdu = data[8:8 + l2len]
        if want is not None and handle != want:
            seen_handles[handle] = seen_handles.get(handle, 0) + 1
            continue
        seen_handles[handle] = seen_handles.get(handle, 0) + 1
        if t0 is None:
            t0 = ts
        rel = (ts - t0) / 1e6
        direction = "TX" if opcode == OP_ACL_TX else "RX"
        print(f"{rel:8.3f} h{handle} {direction}  {decode_att(pdu)}")

    print("\nATT packets per connection handle:",
          ", ".join(f"h{h}={n}" for h, n in sorted(seen_handles.items())) or "none")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
