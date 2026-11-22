#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["python-osc", "matplotlib"]
# ///
"""Receive OSC (e.g. from the Data OSC iPhone app) and plot the joystick state.

Point the app at this machine (Tailscale IP) and --port. Every OSC address
received is listed in the window with its latest values, so you can see the
app's naming scheme. The stick panels follow --left / --right, which take
either one address carrying x,y as its first two args, or two comma-separated
addresses carrying one value each:

    uv run scripts/osc_test.py --port 8000
    uv run scripts/osc_test.py --left /gc/leftThumbstick --right /gc/rightThumbstick
    uv run scripts/osc_test.py --left /gc/lx,/gc/ly --right /gc/rx,/gc/ry

Without --left / --right, the first addresses containing "left"/"right" and
"stick" are used. A dot turns grey after STALE_S without an update.
"""

import argparse
import threading
import time

import matplotlib.pyplot as plt
from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import ThreadingOSCUDPServer

STALE_S = 0.5
latest = {}  # address -> (args, monotonic receive time)
lock = threading.Lock()


def on_message(address, *args):
    with lock:
        if address not in latest:
            print(f"new address {address}  {args}")
        latest[address] = (args, time.monotonic())


def guess(side):
    """Address(es) for a stick, from names containing `side` and 'stick'."""
    names = sorted(a for a in latest if side in a.lower() and "stick" in a.lower())
    for a in names:
        if len(latest[a][0]) >= 2:
            return [a]
    xs = [a for a in names if a.lower().endswith("x")]
    ys = [a for a in names if a.lower().endswith("y")]
    return [xs[0], ys[0]] if xs and ys else None


def stick(addrs):
    """(x, y, age) for one stick, or None if not received yet."""
    try:
        if len(addrs) == 1:
            args, t = latest[addrs[0]]
            return float(args[0]), float(args[1]), time.monotonic() - t
        (ax, tx), (ay, ty) = latest[addrs[0]], latest[addrs[1]]
        return float(ax[0]), float(ay[0]), time.monotonic() - min(tx, ty)
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--host", default="0.0.0.0", help="address to listen on")
    p.add_argument("--port", type=int, default=8000, help="UDP port to listen on")
    p.add_argument("--left", help="left stick: 'addr' (args x,y) or 'addr_x,addr_y'")
    p.add_argument("--right", help="same, for the right stick")
    args = p.parse_args()
    fixed = {"left": args.left and args.left.split(","),
             "right": args.right and args.right.split(",")}

    disp = Dispatcher()
    disp.set_default_handler(on_message)
    server = ThreadingOSCUDPServer((args.host, args.port), disp)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"listening for OSC on {args.host}:{args.port}")

    fig = plt.figure(figsize=(10, 4))
    grid = fig.add_gridspec(1, 3, width_ratios=[1, 1, 1.3])
    dots, titles = {}, {}
    for i, side in enumerate(("left", "right")):
        ax = fig.add_subplot(grid[i])
        ax.set(xlim=(-1.05, 1.05), ylim=(-1.05, 1.05), aspect="equal")
        ax.axhline(0, color="0.85", lw=0.8)
        ax.axvline(0, color="0.85", lw=0.8)
        dots[side] = ax.plot([0], [0], "o", ms=12, color="0.6")[0]
        titles[side] = ax.set_title(f"{side}: no address", fontsize=9)
    listing = fig.add_subplot(grid[2])
    listing.axis("off")
    text = listing.text(0, 1, "waiting for OSC...", va="top", family="monospace", fontsize=8)
    fig.tight_layout()

    try:
        while plt.fignum_exists(fig.number):
            with lock:
                for side in ("left", "right"):
                    addrs = fixed[side] or guess(side)
                    s = stick(addrs) if addrs else None
                    if s:
                        x, y, age = s
                        dots[side].set_data([x], [y])
                        dots[side].set_color("0.6" if age > STALE_S else "#2a78d6")
                        titles[side].set_text(f"{side} ({x:+.2f}, {y:+.2f})\n"
                                              f"{','.join(addrs)}")
                    elif addrs:
                        titles[side].set_text(f"{side}: waiting\n{','.join(addrs)}")
                now = time.monotonic()
                rows = [f"{a[-28:]:28} {' '.join(f'{v:+.2f}' if isinstance(v, float) else str(v) for v in vals[:4])}"
                        f"{'  (stale)' if now - t > STALE_S else ''}"
                        for a, (vals, t) in sorted(latest.items())]
            if rows:
                text.set_text("\n".join(rows[:40]))
            plt.pause(0.05)
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()


if __name__ == "__main__":
    main()
