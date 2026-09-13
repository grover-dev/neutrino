# Dyness 12V LFP BMS — register reference

Modbus RTU over a BLE serial pipe. Slave `0x01`, function `0x03` only.
From a 4S 100Ah pack, firmware `P4S100A-50565-1.50`.

## Transport

| | |
|---|---|
| Service | `00002760-08c2-11e1-9073-0e8ac72e1001` |
| TX | `…0e8ac72e0001` — write **without response** |
| RX | `…0e8ac72e0002` — notify |

Single connection only. Requires **BLE 4.0 central** — write-without-response and
notify, no pairing or encryption.

## Framing

```
request   [0x01, 0x03, addr_be:u16, count_be:u16, crc_le:u16]        // 8 B
response  [0x01, 0x03, byte_count:u8, data[byte_count], crc_le:u16]  // 3+n+2
exception [0x01, 0x83, code:u8, crc_le:u16]                          // 5 B
```

- Registers are **big-endian u16**; CRC is **little-endian**.
- CRC-16/Modbus: init `0xFFFF`, poly `0xA001`, reflected.
- Replies arrive **fragmented** across notifications. Buffer until
  `3 + buf[2] + 2` bytes, then check CRC. Resync by dropping bytes until
  `buf[0] == 0x01 && (buf[1] == 0x03 || buf[1] == 0x83)`.
- Fragmentation is the serial bridge flushing its buffer (~20 B), not the ATT MTU:
  the pack accepts MTU 517 and still splits a 21 B reply 20 + 1. Negotiating a
  larger MTU changes nothing — never size buffers off it.

## Registers

| Addr | Count | Type | Scale | Field |
|---|---|---|---|---|
| `0x0000` | 1 | i16 | ÷100 | current, A (+ charge / − discharge) |
| `0x0001` | 1 | u16 | ÷100 | voltage, V |
| `0x0002` | 1 | u16 | — | SOC, % |
| `0x0003` | 1 | u16 | — | SOH, % |
| `0x0004` | 1 | u16 | ÷100 | remaining capacity, Ah |
| `0x0005` | 1 | u16 | ÷100 | full capacity, Ah |
| `0x0006` | 1 | u16 | ÷100 | design capacity, Ah |
| `0x0007` | 1 | u16 | — | cycle count |
| `0x0009` | 1 | u16 | — | alarm bitfield (0 = none) |
| `0x000A` | 1 | u16 | — | protection bitfield (0 = none) |
| `0x000B` | 1 | u16 | — | low byte = fault code; high byte = status flags |
| `0x000F` | 1 | u16 | — | cell count |
| `0x0010` | n | u16 | ÷1000 | cell voltages, V |
| `0x002E` | 1 | u16 | — | temp sensor count |
| `0x002F` | 10 | i16 | ÷10 | temperature slots, °C (unused read 0) |
| `0x0039` | 1 | i16 | ÷10 | MOSFET temperature, °C |
| `0x003A` | 1 | i16 | ÷10 | aux temperature |
| `0x00AA` | 9 | ascii | — | firmware version, 18 B |
| `0x00B4` | 10 | ascii | — | serial number, 20 B |
| `0x00BE` | 10 | ascii | — | model, 20 B (blank on this firmware) |

`0x000B` status flags, high byte: `0x04` charge MOSFET on, `0x08` discharge
MOSFET on (`0x0400` / `0x0800` on the full u16; idle pack reads `0x0C00`).

Sentinels: `0xC000` = register readable but unimplemented, never decode as data.
`0xFFFF` = temperature slot with no sensor fitted.

## Read sequence

1. `0x0000` × 12 — status through the flags word.
2. `0x000F` × 1 → cell count `n`, then `0x0010` × `n`.
3. `0x002E` × 11 — sensor count plus the 10 slots.
4. `0x0039` × 2 — MOSFET and aux temperature.
5. `0x00AA` × 30 in chunks of 5 — device strings; read once, not per poll.

Rules:
- **The map is sparse** (`0x003B` returns exception `0x03`). A request spanning a
  nonexistent register fails entirely — never read blind ranges; probe with
  single-register reads.
- Counts of 1, 5, 6, 7, 8, 11 and 12 are confirmed. Read the cell count before
  the cell voltages rather than over-reading.
- ASCII fields are space- and NUL-padded; trim both.

## Unresolved

- Lifetime charge/discharge counters: app polls `0x003F` × 6 →
  `0000 0000 C000 C000 0000 0000`. Both counters read zero, so the layout is
  unconfirmed.
- ACin and heater bits in `0x000B` were never set in any capture.
