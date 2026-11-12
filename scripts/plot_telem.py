#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Plot a telem_view.py --record CSV as a standalone HTML page.

- GPS track on a map (from the *.latitude / *.longitude columns; rows with no
  fix, i.e. 0,0 or <section>.active == False, are left out).
- One small chart per numeric field, grouped by blackboard section, all on
  the rx_iso receive-time axis. Booleans and integers plot as steps; fields
  that never change are listed as text.

Two modes, switchable in the page (--mode picks the initial one):
  all     every sample at once; drag to zoom (all charts follow), hover a
          chart to show that moment's position on the map
  replay  plays the run back against its receive timestamps at a chosen
          speed; packet gaps longer than GAP_SECS play out as gaps (lines
          break, the map marker stops, the status shows "no data")

The page is fully self-contained and works offline:
  - plotly.js is downloaded once from jsDelivr, cached in ~/.cache/neutrino/,
    and embedded.
  - Basemap tiles (Esri street map and/or satellite) covering the track are downloaded
    when there is internet, cached in ~/.cache/neutrino/tiles/, and embedded.
    Without internet, whatever is already cached for the area is used; with
    nothing cached the track is drawn on a blank background.

Usage:
    uv run scripts/plot_telem.py run.csv [-o out.html] [--mode replay] [--speed 5] [--open]
"""

import argparse
import base64
import csv
import json
import math
import os
import socket
import sys
import threading
import urllib.error
import urllib.request
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

PLOTLY_VERSION = "2.35.2"
PLOTLY_URL = f"https://cdn.jsdelivr.net/npm/plotly.js-dist-min@{PLOTLY_VERSION}/plotly.min.js"
CACHE = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "neutrino"
GAP_SECS = 1.0  # break lines where no packet arrived for this long

# Raster basemap tiles (Esri public basemaps, JPEG, {z}/{y}/{x} order), cached per tile.
ESRI = "https://services.arcgisonline.com/ArcGIS/rest/services/{}/MapServer/tile/{{z}}/{{y}}/{{x}}"
BASEMAPS = {
    "streets": ESRI.format("World_Street_Map"),
    "satellite": ESRI.format("World_Imagery"),
}
TILE_CACHE = CACHE / "tiles"
TILE_UA = "neutrino-telemetry-plot/1.0"
MAX_ZOOM = 19


def merc(lat, lon):
    """lat/lon -> normalized Web Mercator (X east 0..1, Y north 0..1)."""
    s = math.sin(math.radians(max(-85.05, min(85.05, lat))))
    return (lon + 180.0) / 360.0, 0.5 + math.log((1 + s) / (1 - s)) / (4 * math.pi)


def plan_tiles(track, max_tiles):
    """{zoom: {(x, y), ...}} covering the track, coarse levels first.

    Levels up to the one that fits the whole track cover its bounding box,
    widened 4x east-west to fill the wide map panel; finer levels only cover
    a corridor one tile wide around the track. Stops before the level that
    would push the total past max_tiles.
    """
    x0, x1, y0, y1 = track["bbox"]
    span = max(x1 - x0, y1 - y0)
    cx = (x0 + x1) / 2
    x0, x1 = cx - 2 * span, cx + 2 * span
    pts = [(x, y) for x, y in zip(track["X"], track["Y"]) if x is not None]
    z_fit = max(2, min(MAX_ZOOM - 1, round(math.log2(4 / span))))
    plan, total = {}, 0
    for z in range(max(1, z_fit - 3), min(MAX_ZOOM, z_fit + 3) + 1):
        n = 2 ** z

        def idx(x, y):
            return min(n - 1, int(x * n)), min(n - 1, int((1 - y) * n))

        tiles = set()
        if z <= z_fit:
            (ax0, ay1), (ax1, ay0) = idx(x0, y0), idx(x1, y1)
            cx, cy = idx((x0 + x1) / 2, (y0 + y1) / 2)
            for tx in range(min(ax0, cx - 1), max(ax1, cx + 1) + 1):
                for ty in range(min(ay0, cy - 1), max(ay1, cy + 1) + 1):
                    tiles.add((tx, ty))
        else:
            for x, y in pts:
                tx, ty = idx(x, y)
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        tiles.add((tx + dx, ty + dy))
        tiles = {(tx, ty) for tx, ty in tiles if 0 <= tx < n and 0 <= ty < n}
        if total + len(tiles) > max_tiles:
            break
        plan[z] = tiles
        total += len(tiles)
    return plan


def fetch_tiles(plan, style, offline):
    """Download missing tiles into the cache (unless offline), then return
    {zoom: [[x, y, base64_png], ...]} for every tile available in the cache."""
    def path(z, x, y):
        return TILE_CACHE / style / str(z) / str(x) / f"{y}.jpg"

    todo = [(z, x, y) for z, ts in plan.items() for x, y in ts if not path(z, x, y).exists()]
    got = 0
    lock = threading.Lock()

    def get(job, tries=2):
        """True if the tile is now cached."""
        nonlocal got
        z, x, y = job
        url = BASEMAPS[style].format(z=z, x=x, y=y)
        req = urllib.request.Request(url, headers={"User-Agent": TILE_UA})
        for _ in range(tries):
            try:
                with urllib.request.urlopen(req, timeout=10) as r:
                    data = r.read()
                break
            except urllib.error.HTTPError:
                return False  # server has no such tile; don't retry
            except (urllib.error.URLError, socket.timeout, OSError):
                continue
        else:
            return False
        p = path(z, x, y)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(p)
        with lock:
            got += 1
        return True

    # One probe request decides whether we are online; after that a failure
    # only skips that tile.
    if todo and not offline.is_set():
        if not get(todo[0], tries=1):
            offline.set()
        else:
            with ThreadPoolExecutor(8) as pool:
                list(pool.map(get, todo[1:]))

    out, have = {}, 0
    for z, ts in plan.items():
        for x, y in sorted(ts):
            p = path(z, x, y)
            if p.exists():
                out.setdefault(z, []).append([x, y, base64.b64encode(p.read_bytes()).decode()])
                have += 1
    want = sum(len(ts) for ts in plan.values())
    print(f"  {style} tiles: {have}/{want} available ({got} downloaded)")
    return out


def plotly_js():
    path = CACHE / f"plotly-{PLOTLY_VERSION}.min.js"
    if not path.exists():
        print(f"downloading plotly.js {PLOTLY_VERSION} -> {path}")
        CACHE.mkdir(parents=True, exist_ok=True)
        try:
            with urllib.request.urlopen(PLOTLY_URL, timeout=30) as r:
                data = r.read()
        except OSError as e:
            sys.exit(f"could not download plotly.js ({e}); rerun with internet once, "
                     f"or pass --cdn")
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)
    return path.read_text(encoding="utf-8")


def to_num(s):
    if s == "":
        return None
    if s == "True":
        return 1
    if s == "False":
        return 0
    try:
        return float(s)
    except ValueError:
        raise TypeError


def load(path):
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        sys.exit(f"{path}: no rows")
    if "rx_iso" not in rows[0] or "rx_unix" not in rows[0]:
        sys.exit(f"{path}: no rx_iso/rx_unix columns, not a telem_view.py recording?")

    t0 = float(rows[0]["rx_unix"])

    # Insert an empty row inside each receive gap so lines break there
    # instead of drawing straight across dropped telemetry.
    gapped = [rows[0]]
    for prev, cur in zip(rows, rows[1:]):
        if float(cur["rx_unix"]) - float(prev["rx_unix"]) > GAP_SECS:
            gapped.append({k: "" for k in cur} | {"rx_iso": prev["rx_iso"],
                                                   "rx_unix": prev["rx_unix"]})
        gapped.append(cur)
    rows = gapped

    x = [r["rx_iso"] for r in rows]
    t = [round(float(r["rx_unix"]) - t0, 3) for r in rows]

    cols = list(rows[0])
    lat_col = next((c for c in cols if c.endswith("latitude")), None)
    lon_col = next((c for c in cols if c.endswith("longitude")), None)
    track = None
    if lat_col and lon_col:
        active_col = lat_col.rpartition(".")[0] + ".active"
        lat, lon = [], []
        for r in rows:
            try:
                la, lo = float(r[lat_col]), float(r[lon_col])
            except ValueError:
                la = lo = None
            if (la == 0 and lo == 0) or r.get(active_col) == "False":
                la = lo = None
            lat.append(la)
            lon.append(lo)
        if any(v is not None for v in lat):
            X, Y = [], []
            for la, lo in zip(lat, lon):
                mx, my = merc(la, lo) if la is not None else (None, None)
                X.append(mx)
                Y.append(my)
            xs = [v for v in X if v is not None]
            ys = [v for v in Y if v is not None]
            # view box: track extent + 10% margin, at least 2^-17 (~300 m) wide
            span = max(max(xs) - min(xs), max(ys) - min(ys), 2 ** -17) * 1.2
            cx, cy = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
            track = {"lat": lat, "lon": lon, "X": X, "Y": Y,
                     "bbox": [cx - span / 2, cx + span / 2, cy - span / 2, cy + span / 2]}

    groups, constants = {}, {}
    for col in cols:
        if col in ("rx_unix", "rx_iso"):
            continue
        raw = [r[col] for r in rows]
        try:
            ys = [to_num(v) for v in raw]
        except TypeError:
            continue  # non-numeric column
        section, _, field = col.rpartition(".")
        section = section or "blackboard"
        vals = [v for v in ys if v is not None]
        if len(set(vals)) <= 1:
            constants.setdefault(section, []).append([field, next((v for v in raw if v), "")])
            continue
        is_bool = all(v in ("True", "False", "") for v in raw)
        is_flag = not is_bool and set(vals) <= {0, 1}       # u32 used as a bool
        is_int = all(float(v).is_integer() for v in vals)   # counters, percents
        groups.setdefault(section, []).append({
            "name": field, "y": ys,
            "bool": is_bool, "flag": is_flag, "step": is_bool or is_int,
        })
    return {"x": x, "t": t, "t0": t0, "gap": GAP_SECS, "track": track,
            "groups": groups, "constants": constants}


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Telemetry Plots</title>
__PLOTLY_TAG__
<style>
:root {
  color-scheme: light;
  --surface-0: #f5f5f3;
  --surface-1: #fcfcfb;
  --grid: #e6e5e1;
  --text-primary: #0b0b0b;
  --text-secondary: #52514e;
  --text-muted: #7a7974;
  --series-1: #2a78d6;
  --good: #0ca30c;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --surface-0: #121211;
    --surface-1: #1a1a19;
    --grid: #2c2c2a;
    --text-primary: #f0efec;
    --text-secondary: #c3c2b7;
    --text-muted: #8f8e86;
    --series-1: #3987e5;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --surface-0: #121211;
  --surface-1: #1a1a19;
  --grid: #2c2c2a;
  --text-primary: #f0efec;
  --text-secondary: #c3c2b7;
  --text-muted: #8f8e86;
  --series-1: #3987e5;
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 0 16px 48px;
  background: var(--surface-0); color: var(--text-primary);
  font: 14px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif;
}
main { max-width: 1400px; margin: 0 auto; }
h1 { font-size: 20px; margin: 24px 0 4px; }
.meta { color: var(--text-secondary); margin: 0 0 12px; }
h2 { font-size: 15px; margin: 32px 0 12px; font-family: ui-monospace, monospace; }
.bar {
  position: sticky; top: 0; z-index: 10;
  display: flex; flex-wrap: wrap; align-items: center; gap: 8px 12px;
  padding: 10px 0; background: var(--surface-0); border-bottom: 1px solid var(--grid);
}
.seg { display: inline-flex; border: 1px solid var(--grid); border-radius: 6px; overflow: hidden; }
.seg button { border: 0; border-radius: 0; }
.seg button[aria-pressed="true"] { background: var(--series-1); color: #fff; }
button, select {
  font: inherit; color: var(--text-primary); background: var(--surface-1);
  border: 1px solid var(--grid); border-radius: 6px; padding: 4px 10px; cursor: pointer;
}
label { color: var(--text-secondary); font-size: 13px; }
#scrub { flex: 1 1 200px; min-width: 120px; accent-color: var(--series-1); }
.clock { font-family: ui-monospace, monospace; font-size: 13px; }
.status { font-size: 13px; color: var(--text-secondary); }
.status.live { color: var(--text-primary); }
.status.live::before { content: "\25CF "; color: var(--good); }
.status.gap::before { content: "\25CB "; }
body.mode-all .replay-only { display: none; }
.grid {
  display: grid; gap: 12px;
  grid-template-columns: repeat(auto-fill, minmax(min(100%, 420px), 1fr));
}
.card {
  background: var(--surface-1); border: 1px solid var(--grid);
  border-radius: 8px; padding: 10px 12px 4px; min-width: 0; overflow: hidden;
}
.card h3 { font-size: 13px; font-weight: 600; margin: 0; font-family: ui-monospace, monospace; }
.chart { height: 190px; width: 100%; }
#map { height: 380px; width: 100%; }
.note { color: var(--text-muted); font-size: 12px; margin: 6px 0 4px; }
.consts { color: var(--text-muted); font-size: 12px; margin: 8px 0 0;
  font-family: ui-monospace, monospace; }
</style>
</head>
<body>
<main>
  <h1>Telemetry</h1>
  <p class="meta" id="meta"></p>
  <div class="bar">
    <div class="seg" role="group" aria-label="Mode">
      <button data-mode="all">All data</button>
      <button data-mode="replay">Replay</button>
    </div>
    <button class="replay-only" id="play">Play</button>
    <label class="replay-only">speed
      <select id="speed">
        <option value="1">1x</option><option value="2">2x</option><option value="5">5x</option>
        <option value="10">10x</option><option value="30">30x</option><option value="100">100x</option>
      </select></label>
    <label class="replay-only">window
      <select id="win">
        <option value="30">30 s</option><option value="120">2 min</option>
        <option value="600">10 min</option><option value="0">whole run</option>
      </select></label>
    <input class="replay-only" id="scrub" type="range" min="0" step="0.1" value="0" aria-label="Replay position">
    <span class="replay-only clock" id="clock"></span>
    <span class="replay-only status" id="status"></span>
  </div>
  <div id="root"></div>
</main>
<script>
const DATA = __DATA__;
const OPTS = __OPTS__;

const N = DATA.x.length, T = DATA.t, DUR = T[N - 1];
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const charts = [];
let mapEl = null;
let mode = OPTS.mode;
let cursor = 0, shown = N, playing = false;

// ---------- helpers ----------
const pad = (n, w = 2) => String(n).padStart(w, "0");
function fmtTime(sec) {  // local time string Plotly parses on a date axis
  const d = new Date((DATA.t0 + sec) * 1000);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
         `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}.${pad(d.getMilliseconds(), 3)}`;
}
function upper(c) {  // number of rows with T <= c
  let lo = 0, hi = N;
  while (lo < hi) { const m = (lo + hi) >> 1; if (T[m] <= c) lo = m + 1; else hi = m; }
  return lo;
}
const lastValid = new Int32Array(N);  // last row <= i with a GPS fix, or -1
if (DATA.track) {
  let lv = -1;
  for (let i = 0; i < N; i++) { if (DATA.track.lat[i] != null) lv = i; lastValid[i] = lv; }
}
function yRange(s) {
  let lo = Infinity, hi = -Infinity;
  for (const v of s.y) if (v != null) { if (v < lo) lo = v; if (v > hi) hi = v; }
  const p = (hi - lo) * 0.06 || 1;
  return [lo - p, hi + p];
}
const sl = (a, n) => n >= N ? a : a.slice(0, n);

// ---------- charts ----------
function layout(s) {
  const axis = {
    gridcolor: css("--grid"), linecolor: css("--grid"), zeroline: false,
    tickfont: { color: css("--text-secondary"), size: 11 }, automargin: true,
  };
  const replay = mode === "replay";
  const y = (s.bool || s.flag)
    ? { ...axis, tickvals: [0, 1], ticktext: s.bool ? ["false", "true"] : ["0", "1"], range: [-0.15, 1.15] }
    : { ...axis, exponentformat: "none", ...(replay ? { range: s.yr, autorange: false } : {}) };
  return {
    margin: { l: 8, r: 8, t: 6, b: 8 },
    paper_bgcolor: css("--surface-1"), plot_bgcolor: css("--surface-1"),
    font: { family: "system-ui, sans-serif", color: css("--text-primary") },
    hovermode: "x", hoverlabel: {
      bgcolor: css("--surface-1"), bordercolor: css("--grid"), font: { color: css("--text-primary") },
    },
    showlegend: false,
    xaxis: { ...axis, type: "date", showspikes: true, spikemode: "across", spikethickness: 1,
             spikecolor: css("--text-muted"), spikedash: "solid",
             fixedrange: replay, ...(replay ? { range: xRange(), autorange: false } : {}) },
    yaxis: { ...y, fixedrange: replay },
  };
}
function trace(s, n) {
  return {
    x: sl(DATA.x, n), y: sl(s.y, n), name: s.name,
    type: N > 5000 ? "scattergl" : "scatter",
    mode: "lines", connectgaps: false,
    line: { color: css("--series-1"), width: 2, shape: s.step ? "hv" : "linear" },
    hovertemplate: s.bool ? "%{x}<br>%{y}<extra></extra>" : "%{x}<br>%{y:.4~g}<extra></extra>",
  };
}
function xRange() {
  const win = +document.getElementById("win").value;
  return win ? [fmtTime(cursor - win), fmtTime(cursor)] : [fmtTime(0), fmtTime(DUR)];
}

// ---------- map ----------
// The track and the embedded tiles share normalized Web Mercator coordinates
// (X east 0..1, Y north 0..1) on a plain cartesian plot, so the basemap needs
// no network. Tile zoom level follows the view.
let markerIdx = -1;   // row shown by the position marker (-1 = hidden)
let mapRange = null;  // current view [x0, x1, y0, y1]
for (const st of Object.values(DATA.tiles))
  for (const lvl of Object.values(st)) for (const t of lvl) t[2] = "data:image/jpeg;base64," + t[2];
if (DATA.track) DATA.track.ll = DATA.track.lat.map((v, i) => [v, DATA.track.lon[i]]);
let basemap = Object.keys(DATA.tiles)[0] || null;

function tileSet() { return basemap ? DATA.tiles[basemap] : null; }
function tileImages(r) {
  const ts = tileSet();
  if (!ts) return [];
  const levels = Object.keys(ts).map(Number).sort((a, b) => a - b);
  // finest level whose tiles are not smaller on screen than ~1 tile pixel per screen pixel
  const want = Math.log2((mapEl.clientWidth || 800) / 256 / (r[1] - r[0]));
  let z = levels[0];
  for (const l of levels) if (l <= Math.ceil(want)) z = l;
  const n = 2 ** z, out = [];
  // street map is bright: dim it on a dark page; satellite stays as is
  const opacity = basemap === "streets" && css("color-scheme") === "dark" ? 0.6 : 1;
  for (const [x, y, src] of ts[z]) {
    const x0 = x / n, x1 = (x + 1) / n, top = 1 - y / n, bot = 1 - (y + 1) / n;
    if (x1 < r[0] || x0 > r[1] || top < r[2] || bot > r[3]) continue;
    out.push({ source: src, xref: "x", yref: "y", x: x0, y: top, sizex: 1 / n, sizey: 1 / n,
               xanchor: "left", yanchor: "top", sizing: "stretch", layer: "below", opacity });
  }
  return out;
}
function mapTraces(n) {
  const tr = DATA.track, i = markerIdx;
  const hover = "%{text}<br>%{customdata[0]:.6f}, %{customdata[1]:.6f}<extra></extra>";
  return [
    { type: N > 5000 ? "scattergl" : "scatter", mode: "lines", connectgaps: false,
      x: sl(tr.X, n), y: sl(tr.Y, n), text: sl(DATA.x, n), customdata: sl(tr.ll, n),
      line: { color: css("--series-1"), width: 2 }, hovertemplate: hover },
    { type: "scatter", mode: "markers",
      x: i >= 0 ? [tr.X[i]] : [], y: i >= 0 ? [tr.Y[i]] : [],
      text: i >= 0 ? [DATA.x[i]] : [], customdata: i >= 0 ? [tr.ll[i]] : [],
      marker: { size: 13, color: css("--series-1"), line: { width: 2, color: css("--surface-1") } },
      hovertemplate: hover },
  ];
}
function mapLayout() {
  const r = mapRange || DATA.track.bbox;
  return {
    margin: { l: 0, r: 0, t: 0, b: 0 }, showlegend: false, dragmode: "pan", hovermode: "closest",
    paper_bgcolor: css("--surface-1"), plot_bgcolor: css("--surface-1"),
    xaxis: { range: [r[0], r[1]], visible: false },
    yaxis: { range: [r[2], r[3]], visible: false, scaleanchor: "x" },
    images: tileImages(r),
    hoverlabel: { bgcolor: css("--surface-1"), bordercolor: css("--grid"), font: { color: css("--text-primary") } },
  };
}
function mapRender() {
  if (mapEl) Plotly.react(mapEl, mapTraces(shown), mapLayout());
}
function moveMarker(i) { markerIdx = i; mapRender(); }
// After a pan/zoom/resize: remember the real view and swap in tiles for it.
function mapViewChanged(ev) {
  if (!Object.keys(ev).some(k => k.startsWith("xaxis") || k.startsWith("yaxis") || k === "autosize")) return;
  const fl = mapEl._fullLayout;
  mapRange = [...fl.xaxis.range, ...fl.yaxis.range];
  Plotly.relayout(mapEl, { images: tileImages(mapRange) });
}

// ---------- drawing ----------
function drawAll(n) {
  shown = n;
  if (mode === "replay") markerIdx = n > 0 ? lastValid[n - 1] : -1;
  for (const c of charts) Plotly.react(c.el, [trace(c.s, n)], layout(c.s));
  mapRender();
}
function advance() {
  const n = upper(cursor);
  if (n < shown) {
    drawAll(n);  // scrubbed backwards: rebuild from scratch
  } else {
    if (n > shown) {
      for (const c of charts)
        Plotly.extendTraces(c.el, { x: [DATA.x.slice(shown, n)], y: [c.s.y.slice(shown, n)] }, [0]);
      shown = n;
      markerIdx = lastValid[n - 1];
      mapRender();
    }
    const r = xRange();
    for (const c of charts) Plotly.relayout(c.el, { "xaxis.range": r });
  }
  updateReadout();
}
function updateReadout() {
  document.getElementById("scrub").value = cursor;
  document.getElementById("clock").textContent =
    `${fmtTime(cursor).slice(11, 21)}  +${cursor.toFixed(1)}s / ${DUR.toFixed(1)}s`;
  const st = document.getElementById("status");
  const age = shown ? cursor - T[shown - 1] : Infinity;
  const gap = !shown || age > DATA.gap;
  st.className = "replay-only status " + (gap ? "gap" : "live");
  st.textContent = gap ? (shown ? `no data for ${age.toFixed(1)} s` : "no data yet") : "receiving";
}

// ---------- replay loop ----------
// 10 Hz tick (the telemetry rate); dt from the clock so speed stays accurate
// even if the browser delays ticks.
let timer = null, lastTick = 0;
function tick() {
  const now = performance.now();
  cursor = Math.min(DUR, cursor + (now - lastTick) / 1000 * +document.getElementById("speed").value);
  lastTick = now;
  advance();
  if (cursor >= DUR) setPlaying(false);
}
function setPlaying(p) {
  playing = p;
  document.getElementById("play").textContent = p ? "Pause" : "Play";
  clearInterval(timer);
  timer = null;
  if (p) {
    if (cursor >= DUR) { cursor = 0; drawAll(0); }
    lastTick = performance.now();
    timer = setInterval(tick, 100);
  }
}
function setMode(m) {
  mode = m;
  setPlaying(false);
  document.body.className = "mode-" + m;
  for (const b of document.querySelectorAll(".seg button"))
    b.setAttribute("aria-pressed", String(b.dataset.mode === m));
  if (m === "all") { markerIdx = -1; drawAll(N); }
  else { drawAll(upper(cursor)); updateReadout(); }
}

// ---------- zoom sync (all mode) ----------
let syncing = false;
function sync(src, ev) {
  if (syncing || mode !== "all") return;
  let upd = null;
  if ("xaxis.range[0]" in ev) upd = { "xaxis.range": [ev["xaxis.range[0]"], ev["xaxis.range[1]"]] };
  else if (ev["xaxis.range"]) upd = { "xaxis.range": ev["xaxis.range"] };
  else if (ev["xaxis.autorange"]) upd = { "xaxis.autorange": true };
  if (!upd) return;
  syncing = true;
  Promise.all(charts.filter(c => c.el !== src).map(c => Plotly.relayout(c.el, upd)))
    .finally(() => { syncing = false; });
}

// ---------- build ----------
function card(parent, title, cls) {
  const c = document.createElement("div");
  c.className = "card";
  const h = document.createElement("h3");
  h.textContent = title;
  const el = document.createElement("div");
  el.className = cls;
  c.append(h, el);
  parent.appendChild(c);
  return el;
}
function build() {
  const root = document.getElementById("root");
  document.getElementById("meta").textContent =
    `${N} samples, ${DATA.x[0]} to ${DATA.x[N - 1]} (${DUR.toFixed(1)} s).`;
  const scrub = document.getElementById("scrub");
  scrub.max = DUR;
  const speed = document.getElementById("speed");
  speed.value = String(OPTS.speed);
  if (speed.value !== String(OPTS.speed)) speed.add(new Option(OPTS.speed + "x", OPTS.speed, true, true));

  if (DATA.track) {
    const h = document.createElement("h2");
    h.textContent = "position";
    root.appendChild(h);
    mapEl = card(root, "GPS track", "chart");
    mapEl.id = "map";
    const note = document.createElement("p");
    note.className = "note";
    note.textContent = basemap
      ? "Basemap: Esri (sources: Esri, Maxar, Earthstar Geographics, OpenStreetMap contributors, " +
        "and the GIS user community). Scroll to zoom, drag to pan, double-click to reset."
      : "No cached map tiles for this area: track shown without a basemap. " +
        "Regenerate the page with internet access to download them.";
    mapEl.parentElement.appendChild(note);
    if (Object.keys(DATA.tiles).length > 1) {
      const pick = document.createElement("select");
      pick.setAttribute("aria-label", "Basemap");
      for (const k of Object.keys(DATA.tiles)) pick.add(new Option(k, k));
      pick.onchange = () => { basemap = pick.value; mapRender(); };
      pick.style.marginTop = "6px";
      mapEl.parentElement.appendChild(pick);
    }
  }

  const sections = [...new Set([...Object.keys(DATA.groups), ...Object.keys(DATA.constants)])];
  for (const sec of sections) {
    const h = document.createElement("h2");
    h.textContent = sec;
    root.appendChild(h);
    const grid = document.createElement("div");
    grid.className = "grid";
    root.appendChild(grid);
    for (const s of DATA.groups[sec] || []) {
      s.yr = yRange(s);
      charts.push({ el: card(grid, s.name, "chart"), s });
    }
    if (DATA.constants[sec]) {
      const p = document.createElement("p");
      p.className = "consts";
      p.textContent = "constant: " + DATA.constants[sec].map(([k, v]) => `${k}=${v}`).join("  ");
      root.appendChild(p);
    }
  }

  const config = { displayModeBar: false, responsive: true };
  for (const c of charts) {
    Plotly.newPlot(c.el, [trace(c.s, N)], layout(c.s), config);
    c.el.on("plotly_relayout", ev => sync(c.el, ev));
    c.el.on("plotly_hover", ev => { if (mode === "all" && DATA.track) moveMarker(lastValid[ev.points[0].pointIndex]); });
  }
  if (mapEl) {
    Plotly.newPlot(mapEl, mapTraces(N), mapLayout(), { displayModeBar: false, responsive: true, scrollZoom: true });
    mapEl.on("plotly_relayout", mapViewChanged);
    mapViewChanged({ autosize: true });  // pick tiles for the real (aspect-corrected) view
  }

  // Plotly only listens to window resize; also follow card width changes.
  const ro = new ResizeObserver(es => { for (const e of es) Plotly.Plots.resize(e.target); });
  for (const c of charts) ro.observe(c.el);
  if (mapEl) ro.observe(mapEl);

  for (const b of document.querySelectorAll(".seg button")) b.onclick = () => setMode(b.dataset.mode);
  document.getElementById("play").onclick = () => setPlaying(!playing);
  document.getElementById("win").onchange = () => mode === "replay" && advance();
  scrub.oninput = () => { cursor = +scrub.value; advance(); };

  setMode(mode);}

function restyle() { drawAll(shown); }
build();
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", restyle);
new MutationObserver(restyle).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
</script>
</body>
</html>
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("csv", type=Path, help="recording from telem_view.py --record")
    ap.add_argument("-o", "--out", type=Path, help="output HTML (default: <csv>.html)")
    ap.add_argument("--mode", choices=["all", "replay"], default="all", help="initial view")
    ap.add_argument("--speed", type=float, default=1.0, help="initial replay speed")
    ap.add_argument("--cdn", action="store_true",
                    help="link plotly.js from jsDelivr instead of embedding (small file, needs internet)")
    ap.add_argument("--basemap", choices=["streets", "satellite", "both", "none"], default="streets",
                    help="basemap(s) to embed; with both, the page gets a toggle")
    ap.add_argument("--max-tiles", type=int, default=250,
                    help="tile budget per basemap (~20-30 KB each); finest zoom levels are dropped first")
    ap.add_argument("--offline", action="store_true", help="use cached tiles only, no downloads")
    ap.add_argument("--open", action="store_true", help="open in a browser when done")
    args = ap.parse_args()

    data = load(args.csv)
    out = args.out or args.csv.with_suffix(".html")

    data["tiles"] = {}
    if data["track"] and args.basemap != "none":
        plan = plan_tiles(data["track"], args.max_tiles)
        print(f"map tiles: zoom {min(plan)}-{max(plan)}" if plan else "map tiles: none planned")
        offline = threading.Event()
        if args.offline:
            offline.set()
        styles = list(BASEMAPS) if args.basemap == "both" else [args.basemap]
        for style in styles:
            got = fetch_tiles(plan, style, offline)
            if got:
                data["tiles"][style] = got
        if offline.is_set() and not args.offline:
            print("  no network: used cached tiles only")

    if args.cdn:
        tag = f'<script src="{PLOTLY_URL}"></script>'
    else:
        tag = "<script>" + plotly_js().replace("</script", "<\\/script") + "</script>"
    blob = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    speed = int(args.speed) if args.speed.is_integer() else args.speed
    opts = json.dumps({"mode": args.mode, "speed": speed})
    html = (PAGE.replace("__DATA__", blob).replace("__OPTS__", opts)
                .replace("__PLOTLY_TAG__", tag))
    out.write_text(html, encoding="utf-8")

    n_charts = sum(len(v) for v in data["groups"].values())
    gps = "with GPS track" if data["track"] else "no GPS fix in data"
    print(f"{len(data['x'])} rows, {n_charts} charts, {gps} -> {out} "
          f"({out.stat().st_size / 1e6:.1f} MB)")
    if args.open:
        webbrowser.open(out.resolve().as_uri())


if __name__ == "__main__":
    main()
