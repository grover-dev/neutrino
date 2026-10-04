#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Synthetic data source for boat_vis.py.

A toy boat with inertia steers toward a destination and streams
{"lat", "lon", "heading", "dest_lat", "dest_lon"} over UDP. Clicking in the
visualizer sends {"type": "set_dest", ...} back, which retargets the boat.

Usage (two terminals):
    uv run scripts/boat_vis.py
    uv run scripts/boat_sim.py [--host 127.0.0.1] [--port 5005] [--rate 20]
"""

import argparse
import json
import math
import socket
import time

EARTH_R = 6_371_000.0  # m

MAX_SPEED = 2.0  # m/s
MAX_YAW_RATE = 30.0  # deg/s
SPEED_TAU = 3.0  # s, speed lag (inertia)
YAW_TAU = 1.0  # s, yaw-rate lag
ARRIVE_RADIUS = 2.0  # m


def wrap180(a):
    return (a + 180.0) % 360.0 - 180.0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5005)
    ap.add_argument("--rate", type=float, default=20.0, help="updates per second")
    ap.add_argument("--start", default="37.0,-122.0", help="LAT,LON start position")
    ap.add_argument("--heading", type=float, default=0.0, help="start heading, deg from true north")
    ap.add_argument("--dest", default="80,60", help="initial destination as x,y metres east/north of start")
    args = ap.parse_args()

    lat0, lon0 = map(float, args.start.split(","))
    k = math.cos(math.radians(lat0))

    def to_latlon(x, y):
        return lat0 + math.degrees(y / EARTH_R), lon0 + math.degrees(x / (EARTH_R * k))

    def to_xy(lat, lon):
        return math.radians(lon - lon0) * EARTH_R * k, math.radians(lat - lat0) * EARTH_R

    x = y = 0.0
    heading = args.heading
    speed = yaw_rate = 0.0
    dx, dy = map(float, args.dest.split(","))

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setblocking(False)
    target = (args.host, args.port)
    dt = 1.0 / args.rate
    print(f"sending to {args.host}:{args.port} at {args.rate} Hz, ctrl-c to stop")

    while True:
        # Commands from the visualizer.
        while True:
            try:
                data, _ = sock.recvfrom(4096)
            except (BlockingIOError, ConnectionRefusedError):
                break
            msg = json.loads(data)
            if msg.get("type") == "set_dest":
                dx, dy = to_xy(msg["dest_lat"], msg["dest_lon"])
                print(f"new dest: {dx:.1f}, {dy:.1f} m")

        # Dumb controller: point at the destination, slow down near it.
        dist = math.hypot(dx - x, dy - y)
        bearing = math.degrees(math.atan2(dx - x, dy - y)) % 360
        err = wrap180(bearing - heading)
        if dist > ARRIVE_RADIUS:
            yaw_cmd = max(-MAX_YAW_RATE, min(MAX_YAW_RATE, 2.0 * err))
            speed_cmd = min(MAX_SPEED, 0.2 * dist) * max(0.0, math.cos(math.radians(err)))
        else:
            yaw_cmd = speed_cmd = 0.0

        # First-order lags stand in for inertia.
        yaw_rate += (yaw_cmd - yaw_rate) * dt / YAW_TAU
        speed += (speed_cmd - speed) * dt / SPEED_TAU
        heading = (heading + yaw_rate * dt) % 360
        h = math.radians(heading)
        x += speed * math.sin(h) * dt
        y += speed * math.cos(h) * dt

        lat, lon = to_latlon(x, y)
        dlat, dlon = to_latlon(dx, dy)
        pkt = {"lat": lat, "lon": lon, "heading": heading, "dest_lat": dlat, "dest_lon": dlon}
        sock.sendto(json.dumps(pkt).encode(), target)
        time.sleep(dt)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
