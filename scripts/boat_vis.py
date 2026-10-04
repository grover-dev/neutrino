#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["matplotlib"]
# ///
"""2-D visualizer for boat nav/controller testing.

Listens for JSON datagrams on UDP and renders the boat on a local flat plane
(x = east, y = north, metres) centred on an origin lat/lon.

Inbound (model -> visualizer), one JSON object per datagram, any subset of:
    {"lat": 37.0, "lon": -122.0, "heading": 45.0, "dest_lat": 37.001, "dest_lon": -122.0}
  heading is degrees clockwise from true north.

Outbound (visualizer -> model), sent to the address of the last inbound packet:
    {"type": "set_dest", "dest_lat": ..., "dest_lon": ...}   on left-click in the plot
  Future: {"type": "env", ...} for static currents/wind (see send()).

Usage:
    uv run scripts/boat_vis.py [--port 5005] [--origin LAT,LON]
"""

import argparse
import json
import math
import socket
import threading

import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from matplotlib.patches import Polygon

EARTH_R = 6_371_000.0  # m
TRAIL_MAX = 2000


class Plane:
    """Equirectangular projection around an origin; fine over a few km."""

    def __init__(self, lat0, lon0):
        self.lat0, self.lon0 = lat0, lon0
        self.k = math.cos(math.radians(lat0))

    def to_xy(self, lat, lon):
        x = math.radians(lon - self.lon0) * EARTH_R * self.k
        y = math.radians(lat - self.lat0) * EARTH_R
        return x, y

    def to_latlon(self, x, y):
        lat = self.lat0 + math.degrees(y / EARTH_R)
        lon = self.lon0 + math.degrees(x / (EARTH_R * self.k))
        return lat, lon


class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.lat = self.lon = None
        self.heading = 0.0
        self.dest = None  # (lat, lon)
        self.trail = []  # [(lat, lon)]
        self.peer = None  # last sender address, for replies


class Link:
    def __init__(self, port, state):
        self.state = state
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("0.0.0.0", port))
        threading.Thread(target=self._rx, daemon=True).start()

    def _rx(self):
        while True:
            data, addr = self.sock.recvfrom(4096)
            try:
                msg = json.loads(data)
            except json.JSONDecodeError:
                print(f"bad packet from {addr}: {data!r}")
                continue
            s = self.state
            with s.lock:
                s.peer = addr
                if "lat" in msg and "lon" in msg:
                    s.lat, s.lon = float(msg["lat"]), float(msg["lon"])
                    s.trail.append((s.lat, s.lon))
                    del s.trail[:-TRAIL_MAX]
                if "heading" in msg:
                    s.heading = float(msg["heading"])
                if "dest_lat" in msg and "dest_lon" in msg:
                    s.dest = (float(msg["dest_lat"]), float(msg["dest_lon"]))

    def send(self, msg):
        """Send a JSON message to the model. Also the hook for future env forces."""
        with self.state.lock:
            peer = self.state.peer
        if peer is None:
            print("no model connected yet (nothing received), not sent:", msg)
            return
        self.sock.sendto(json.dumps(msg).encode(), peer)


def boat_shape(x, y, heading_deg, size):
    # Arrow-ish hull pointing along +y, then rotated clockwise by heading.
    pts = [(0, 1.0), (0.45, -0.7), (0, -0.4), (-0.45, -0.7)]
    h = math.radians(heading_deg)
    c, s = math.cos(h), math.sin(h)
    # clockwise rotation: (px, py) -> (px*c + py*s, -px*s + py*c)
    return [(x + size * (px * c + py * s), y + size * (-px * s + py * c)) for px, py in pts]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=5005)
    ap.add_argument("--origin", help="LAT,LON of plane origin (default: first fix)")
    ap.add_argument("--min-span", type=float, default=50.0, help="min view width in metres")
    args = ap.parse_args()

    state = State()
    link = Link(args.port, state)
    plane = Plane(*map(float, args.origin.split(","))) if args.origin else None

    fig, ax = plt.subplots(figsize=(8, 8))
    ax.set_aspect("equal")
    ax.set_xlabel("x / east (m)")
    ax.set_ylabel("y / north (m)")
    ax.grid(True, alpha=0.3)
    (trail_line,) = ax.plot([], [], "-", lw=1, color="tab:blue", alpha=0.5)
    (dest_pt,) = ax.plot([], [], "x", ms=12, mew=3, color="tab:red")
    (dest_line,) = ax.plot([], [], ":", color="tab:red", alpha=0.6)
    boat = Polygon([(0, 0)] * 4, closed=True, fc="tab:blue", ec="k")
    ax.add_patch(boat)
    info = ax.text(0.01, 0.99, "", transform=ax.transAxes, va="top", family="monospace")
    ax.set_title(f"waiting for data on udp :{args.port}  (click to set destination)")

    def nav_busy():
        tb = getattr(fig.canvas.manager, "toolbar", None)
        return bool(tb and tb.mode)

    def on_click(ev):
        if ev.inaxes is not ax or ev.button != 1 or plane is None:
            return
        if nav_busy():  # ignore clicks while zooming/panning
            return
        lat, lon = plane.to_latlon(ev.xdata, ev.ydata)
        link.send({"type": "set_dest", "dest_lat": lat, "dest_lon": lon})
        with state.lock:
            state.dest = (lat, lon)  # show immediately; model may echo it back

    fig.canvas.mpl_connect("button_press_event", on_click)

    def update(_):
        nonlocal plane
        with state.lock:
            lat, lon, hdg = state.lat, state.lon, state.heading
            dest, trail = state.dest, list(state.trail)
        if lat is None:
            return ()
        if plane is None:
            plane = Plane(*trail[0])
            ax.set_title(f"origin {plane.lat0:.6f}, {plane.lon0:.6f}  (click to set destination)")

        bx, by = plane.to_xy(lat, lon)
        tx, ty = zip(*(plane.to_xy(*p) for p in trail))
        trail_line.set_data(tx, ty)

        xs, ys = [bx, *tx], [by, *ty]
        txt = f"pos  {lat:.6f}, {lon:.6f}\nxy   {bx:8.1f}, {by:8.1f} m\nhdg  {hdg:6.1f}°"
        if dest:
            dx, dy = plane.to_xy(*dest)
            dest_pt.set_data([dx], [dy])
            dest_line.set_data([bx, dx], [by, dy])
            xs.append(dx)
            ys.append(dy)
            dist = math.hypot(dx - bx, dy - by)
            brg = math.degrees(math.atan2(dx - bx, dy - by)) % 360
            txt += f"\ndest {dist:8.1f} m @ {brg:5.1f}°"
        info.set_text(txt)

        # Auto-fit everything with a margin, unless the user is zoomed/panned.
        if not nav_busy():
            cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
            span = max(max(xs) - min(xs), max(ys) - min(ys), args.min_span) * 1.3
            ax.set_xlim(cx - span / 2, cx + span / 2)
            ax.set_ylim(cy - span / 2, cy + span / 2)
        span = ax.get_xlim()[1] - ax.get_xlim()[0]
        boat.set_xy(boat_shape(bx, by, hdg, span * 0.03))
        return ()

    _anim = FuncAnimation(fig, update, interval=50, cache_frame_data=False)
    plt.show()


if __name__ == "__main__":
    main()
