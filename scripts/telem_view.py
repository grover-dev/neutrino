#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["rich"]
# ///
"""Live terminal view of the Blackboard telemetry sent by src/main.rs.

Each UDP datagram is one JSON-serialized Blackboard. Every top-level key
(motor_telem, gps_telem, bms_telem, ...) becomes its own panel, so new
blackboard members show up without touching this script.

Usage:
    uv run scripts/telem_view.py [--host 127.0.0.1] [--port 34254]
"""

import argparse
import json
import socket
import time

from rich.columns import Columns
from rich.console import Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

STALE_SECS = 1.0  # main loop sends at ~10 Hz


def flatten(obj, prefix=""):
    """Yield (dotted_key, value) for nested dicts/lists."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from flatten(v, f"{prefix}{k}.")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from flatten(v, f"{prefix}{i}.")
    else:
        yield prefix[:-1], obj


def fmt(key, value):
    if isinstance(value, bool):
        return Text(str(value).lower(), style="green" if value else "red")
    if isinstance(value, float):
        digits = 7 if key in ("latitude", "longitude") else 3
        return Text(f"{value:.{digits}f}")
    if value is None:
        return Text("null", style="dim")
    return Text(str(value))


def section(name, data):
    table = Table.grid(padding=(0, 2))
    table.add_column(style="cyan", no_wrap=True)
    table.add_column(justify="right", no_wrap=True)
    for key, value in flatten(data):
        table.add_row(key, fmt(key, value))
    return Panel(table, title=name, title_align="left", expand=False)


def render(bb, stats):
    now = time.monotonic()
    age = now - stats["last_rx"] if stats["last_rx"] else None
    if age is None:
        status = Text("waiting for telemetry...", style="yellow")
    else:
        style = "red bold" if age > STALE_SECS else "green"
        status = Text(
            f"rx {stats['count']}  |  {stats['rate']:.1f} Hz  |  age {age:.2f}s"
            f"  |  bad {stats['bad']}  |  from {stats['src']}",
            style=style,
        )

    panels = []
    if bb:
        wd = bb.get("vehicle_watchdog_telem", {})
        if wd.get("watchdog_lockout_active"):
            status.append("  |  WATCHDOG LOCKOUT", style="red bold reverse")
        if isinstance(bb, dict):
            panels = [section(k, v) for k, v in bb.items()]
        else:
            panels = [section("blackboard", bb)]
    return Group(status, Columns(panels))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default="127.0.0.1", help="address to bind")
    ap.add_argument("--port", type=int, default=34254, help="UDP port to bind")
    args = ap.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((args.host, args.port))
    sock.settimeout(0.1)

    bb = None
    stats = {"count": 0, "bad": 0, "rate": 0.0, "last_rx": None, "src": "-"}

    with Live(render(bb, stats), refresh_per_second=10, screen=False) as live:
        while True:
            try:
                data, addr = sock.recvfrom(65536)
            except socket.timeout:
                live.update(render(bb, stats))
                continue
            try:
                bb = json.loads(data)
            except (json.JSONDecodeError, UnicodeDecodeError):
                stats["bad"] += 1
                continue
            now = time.monotonic()
            if stats["last_rx"]:
                dt = now - stats["last_rx"]
                if dt > 0:
                    # EMA so the rate doesn't jitter every frame
                    stats["rate"] = 0.8 * stats["rate"] + 0.2 * (1.0 / dt)
            stats["last_rx"] = now
            stats["count"] += 1
            stats["src"] = f"{addr[0]}:{addr[1]}"
            live.update(render(bb, stats))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
