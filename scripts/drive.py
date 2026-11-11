#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["pyyaml"]
# ///
"""Keyboard drive: WASD motor duty-cycle commands + background watchdog pet.

Struct/field hashes come from generated/schema/*.yaml. Every command is one
JSON CommandRequest datagram sent to the vehicle's command socket:
    {"command_id": u16, "struct_hash": u32, "field_hash": u32, "value": u64}
f32 values are sent as their IEEE-754 bit pattern (main.rs: f32::from_bits).

Controls (hold to drive; release ramps back to the latched values, else 0):
    w  both motors  -> +max        s  both motors -> -max
    a  motor A      -> +max        d  motor B     -> +max
    l  latch current A/B duty      c  clear latch (ramps back to 0)
    space  stop now (duty 0, clears latch)
    q  quit (duty 0, motors disabled)

A terminal only reports key presses (via autorepeat), not releases, so a key
counts as held until --hold-timeout passes without another repeat.

Usage:
    uv run scripts/drive.py [--host 100.110.162.38] [--max 0.5] [--ramp 0.5]
"""

import argparse
import json
import select
import socket
import struct
import sys
import termios
import threading
import time
import tty
from pathlib import Path

import yaml

RATE_HZ = 10.0
REPO = Path(__file__).resolve().parent.parent


def load_schema(schema_dir):
    """{struct_name: {"hash": int, "fields": {field_name: (hash, type)}}}"""
    out = {}
    for path in sorted(Path(schema_dir).glob("*.yaml")):
        doc = yaml.safe_load(path.read_text())
        out[doc["name"]] = {
            "hash": int(str(doc["hash"]), 0),
            "fields": {
                f["name"]: (int(str(f["hash"]), 0), f["type"]) for f in doc["fields"]
            },
        }
    return out


def encode(value, ty):
    """Pack a value into the u64 that schema::FromRaw64 expects for `ty`."""
    if ty == "f32":
        return struct.unpack("<I", struct.pack("<f", float(value)))[0]
    if ty == "f64":
        return struct.unpack("<Q", struct.pack("<d", float(value)))[0]
    if ty == "bool":
        return 1 if value else 0
    if ty.startswith("i"):
        return int(value) & 0xFFFF_FFFF_FFFF_FFFF  # Rust side: raw as i64
    return int(value)


class Commander:
    def __init__(self, schema, addr):
        self.schema = schema
        self.addr = addr
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.lock = threading.Lock()
        self.next_id = 0
        self.sent = 0

    def send(self, struct_name, field_name, value):
        s = self.schema[struct_name]
        field_hash, ty = s["fields"][field_name]
        with self.lock:
            cmd_id = self.next_id
            self.next_id = (self.next_id + 1) & 0xFFFF
            self.sent += 1
        msg = {
            "command_id": cmd_id,
            "struct_hash": s["hash"],
            "field_hash": field_hash,
            "value": encode(value, ty),
        }
        try:
            self.sock.sendto(json.dumps(msg).encode(), self.addr)
        except OSError:
            pass  # vehicle unreachable; keep trying next tick


def pet_loop(cmd, stop):
    period = 1.0 / RATE_HZ
    next_t = time.monotonic()
    while not stop.is_set():
        cmd.send("VehicleWatchdog", "keep_alive", 1)
        next_t += period
        stop.wait(max(0.0, next_t - time.monotonic()))


def ack_loop(port, stats, stop):
    """Count CommandResponses if they are routed to this machine."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("0.0.0.0", port))
    except OSError:
        stats["acks"] = "n/a"
        return
    sock.settimeout(0.2)
    while not stop.is_set():
        try:
            data, _ = sock.recvfrom(4096)
            resp = json.loads(data).get("response")
        except (socket.timeout, ValueError):
            continue
        key = "ok" if resp == "Success" else "fail"
        stats[key] += 1


def approach(cur, target, step):
    if cur < target:
        return min(cur + step, target)
    return max(cur - step, target)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default="100.110.162.38", help="vehicle address")
    ap.add_argument("--port", type=int, default=34255, help="vehicle command port")
    ap.add_argument("--ack-port", type=int, default=34256, help="local port for responses")
    ap.add_argument("--max", type=float, default=0.5, help="duty-cycle magnitude limit")
    ap.add_argument("--ramp", type=float, default=0.5, help="ramp rate, duty per second")
    ap.add_argument("--hold-timeout", type=float, default=0.6,
                    help="seconds without a key repeat before a key counts as released")
    ap.add_argument("--schema-dir", default=REPO / "generated" / "schema")
    args = ap.parse_args()

    if not 0.0 < args.max <= 1.0:
        sys.exit("--max must be in (0, 1]")

    schema = load_schema(args.schema_dir)
    for s, f in [("MotorControllerCommand", "motor_a_duty_cycle"),
                 ("MotorControllerCommand", "motor_b_duty_cycle"),
                 ("MotorControllerCommand", "enable_motors_bool"),
                 ("VehicleWatchdog", "keep_alive")]:
        if f not in schema.get(s, {}).get("fields", {}):
            sys.exit(f"{s}.{f} not found in {args.schema_dir}")

    cmd = Commander(schema, (args.host, args.port))
    stop = threading.Event()
    stats = {"ok": 0, "fail": 0}
    threading.Thread(target=pet_loop, args=(cmd, stop), daemon=True).start()
    threading.Thread(target=ack_loop, args=(args.ack_port, stats, stop), daemon=True).start()

    m = args.max
    targets = {"w": (m, m), "s": (-m, -m), "a": (m, 0.0), "d": (0.0, m)}
    step = args.ramp / RATE_HZ
    duty_a = duty_b = 0.0
    held, held_at = None, 0.0
    latched = None  # (a, b) to rest at when no key is held

    fd = sys.stdin.fileno()
    old_tty = termios.tcgetattr(fd)
    tty.setcbreak(fd)  # keeps Ctrl-C working
    print(f"driving {args.host}:{args.port}  max={m}  ramp={args.ramp}/s  "
          "[wasd] drive  [l] latch  [c] clear  [space] stop  [q] quit")
    try:
        period = 1.0 / RATE_HZ
        next_t = time.monotonic()
        while True:
            # Drain all pending keys until this tick is due
            while (timeout := next_t - time.monotonic()) > 0:
                ready, _, _ = select.select([sys.stdin], [], [], timeout)
                if not ready:
                    break
                key = sys.stdin.read(1).lower()
                if key == "q":
                    return
                if key == " ":
                    held, latched, duty_a, duty_b = None, None, 0.0, 0.0
                elif key == "l":
                    held, latched = None, (duty_a, duty_b)
                elif key == "c":
                    latched = None
                elif key in targets:
                    held, held_at = key, time.monotonic()
            next_t += period

            if held and time.monotonic() - held_at > args.hold_timeout:
                held = None
            ta, tb = targets[held] if held else (latched or (0.0, 0.0))
            duty_a = approach(duty_a, ta, step)
            duty_b = approach(duty_b, tb, step)

            # Resend everything each tick: a watchdog lockout zeroes motor_command
            cmd.send("MotorControllerCommand", "enable_motors_bool", 1)
            cmd.send("MotorControllerCommand", "motor_a_duty_cycle", duty_a)
            cmd.send("MotorControllerCommand", "motor_b_duty_cycle", duty_b)

            acks = stats.get("acks") or f"ok {stats['ok']} fail {stats['fail']}"
            latch = f"{latched[0]:+.2f}/{latched[1]:+.2f}" if latched else "off"
            sys.stdout.write(
                f"\r key {held or '-'}  A {duty_a:+.2f}  B {duty_b:+.2f}  latch {latch}  "
                f"sent {cmd.sent}  acks {acks}   "
            )
            sys.stdout.flush()
    except KeyboardInterrupt:
        pass
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_tty)
        stop.set()
        for _ in range(3):  # UDP: repeat the stop a few times
            cmd.send("MotorControllerCommand", "motor_a_duty_cycle", 0.0)
            cmd.send("MotorControllerCommand", "motor_b_duty_cycle", 0.0)
            cmd.send("MotorControllerCommand", "enable_motors_bool", 0)
            time.sleep(0.05)
        print("\nstopped: duty 0, motors disabled, watchdog pet stopped")


if __name__ == "__main__":
    main()
