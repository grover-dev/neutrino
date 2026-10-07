# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "open-gopro>=0.22.0",
#     "opencv-python",
# ]
# ///

"""Read GoPro HERO11 config / telemetry over USB, then start the webcam stream.

Usage: uv run scripts/go_pro.py [--res 1080] [--fov wide] [--show]
Without --show the stream runs (for VLC / another consumer at udp://@:8554) until Enter.
HERO11 webcam mode is fixed at 30 fps (max 1080p30) regardless of the camera's video fps setting.
"""

import argparse
import asyncio
import enum
import errno
import json
import signal
import socket
import threading
import time

import cv2 as cv
from open_gopro import WiredGoPro
from open_gopro.models.constants import CameraControl, SettingId, StatusId
from open_gopro.models.streaming import (
    WebcamError,
    WebcamFOV,
    WebcamProtocol,
    WebcamResolution,
    WebcamStatus,
)

RESOLUTIONS = {
    "480": WebcamResolution.RES_480,
    "720": WebcamResolution.RES_720,
    "1080": WebcamResolution.RES_1080,
}
FOVS = {
    "wide": WebcamFOV.WIDE,
    "narrow": WebcamFOV.NARROW,
    "superview": WebcamFOV.SUPERVIEW,
    "linear": WebcamFOV.LINEAR,
}

# HERO11 only supports TS (MPEG-TS over UDP), not RTSP
STREAM_PORT = 8554
# With --show, UdpRelay receives STREAM_PORT (counting bytes for the bitrate) and
# forwards to DECODE_PORT, which OpenCV reads
DECODE_PORT = 8555
# ffmpeg options: don't die on UDP buffer overrun, use a large receive FIFO
DECODE_URL = f"udp://127.0.0.1:{DECODE_PORT}?overrun_nonfatal=1&fifo_size=50000000"

START_ATTEMPTS = 3  # webcam start tries (resetting the camera between) before giving up
STALL_SECONDS = 5.0  # with --show: no stream bytes for this long -> restart the webcam
BAD_STATUS_POLLS = 2  # consecutive polls with webcam not streaming -> restart the webcam

# HERO11 Black only reports setting IDs <= 181; aspect ratio is folded into the
# resolution value (e.g. 27 = 5.3K 4:3). Values verified with gopro_state_diff.py.
# Capture format
VIDEO_SETTINGS = [
    SettingId.VIDEO_RESOLUTION,  # 27 = 5.3K 4:3, 1 = 4K, 4 = 2.7K
    SettingId.FRAMES_PER_SECOND,  # 10 = 24, 5 = 60, 0 = 240
]

# Lens / sensor / image pipeline
SENSOR_SETTINGS = [
    SettingId.VIDEO_LENS,  # 0 = Wide, 3 = SuperView, 4 = Linear
    SettingId.WEBCAM_DIGITAL_LENSES,
    SettingId.HYPERSMOOTH,
    SettingId.VIDEO_HORIZON_LEVELING,
    SettingId.ANTI_FLICKER,
    SettingId.MAX_LENS,
]

# Telemetry
STATUSES = [
    StatusId.INTERNAL_BATTERY_PERCENTAGE,
    StatusId.INTERNAL_BATTERY_BARS,
    StatusId.BATTERY_PRESENT,
    StatusId.USB_CONNECTED,
    StatusId.USB_CONTROLLED,
    StatusId.OVERHEATING,
    StatusId.COLD,
    StatusId.BUSY,
    StatusId.ENCODING,
    StatusId.PREVIEW_STREAM,
    StatusId.PRESET,
    StatusId.SD_CARD_REMAINING,
    StatusId.REMAINING_VIDEO_TIME,
]


def print_section(title: str, state: dict, ids: list) -> None:
    print(f"\n== {title} ==")
    for identifier in ids:
        if identifier in state:
            print(f"  {identifier.name:<30} {state[identifier]}")


def json_value(value):
    # Settings / statuses parse into enums; send their names (e.g. "NUM_1080")
    return value.name if isinstance(value, enum.Enum) else value


class TelemetrySender:
    """Sends one JSON datagram per state poll over UDP.

    UDP so the camera loop never blocks on, or depends on, a listener: if nothing
    is bound to the port, the datagram is just dropped.
    """

    def __init__(self, port: int, stream_config: dict) -> None:
        self._address = ("127.0.0.1", port)
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._stream_config = stream_config

    def send(self, state: dict | None, connection: dict) -> None:
        """Send one message. state=None means the poll failed (camera unreachable)."""

        def section(ids: list) -> dict:
            return {i.name.lower(): json_value(state[i]) for i in ids if i in (state or {})}

        message = {
            "timestamp": time.time(),
            "connection": connection,
            "stream": self._stream_config,
            "video": section(VIDEO_SETTINGS),
            "sensor": section(SENSOR_SETTINGS),
            "telemetry": section(STATUSES),
        }
        try:
            self._socket.sendto(json.dumps(message).encode(), self._address)
        except OSError as e:  # e.g. ECONNREFUSED from an earlier send with no listener
            print(f"telemetry send failed: {e!r}")

    def close(self) -> None:
        self._socket.close()


class LinkHealth:
    """Connection bookkeeping, updated by the state poller and the display, reported in telemetry."""

    def __init__(self) -> None:
        self.last_contact: float | None = None  # monotonic time of last successful camera poll
        self.poll_failures = 0  # consecutive failed polls
        self.last_stream_data: float | None = None  # monotonic time stream bytes last arrived (--show only)
        self.webcam_status: WebcamStatus | None = None

    def as_json(self, webcam: "Webcam") -> dict:
        now = time.monotonic()

        def age(t: float | None) -> float | None:
            return None if t is None else round(now - t, 2)

        stream_age = age(self.last_stream_data)
        if webcam.restarting:
            state = "restarting"
        elif self.poll_failures:
            state = "disconnected"
        elif stream_age is not None and stream_age > STALL_SECONDS:
            state = "stalled"
        else:
            state = "connected"
        return {
            "state": state,
            "seconds_since_contact": age(self.last_contact),
            "consecutive_poll_failures": self.poll_failures,
            "seconds_since_stream_data": stream_age,  # null without --show (stream isn't received)
            "stall_timeout_s": STALL_SECONDS,
            "webcam_status": json_value(self.webcam_status),
            "webcam_restarts": webcam.restarts,
        }


class Webcam:
    """The camera's webcam mode: clean-state start with retries, restart, and stop.

    A previous run that died without webcam_exit, a menu open on the camera, or a
    busy camera all make webcam_start fail (e.g. WebcamError.SET_PRESET), so every
    start resets webcam mode first and retries.
    """

    def __init__(self, gopro: WiredGoPro, resolution: WebcamResolution, fov: WebcamFOV) -> None:
        self._gopro = gopro
        self._resolution = resolution
        self._fov = fov
        self.restarts = 0
        self._lock = asyncio.Lock()

    async def _reset(self) -> None:
        # Errors are expected here (e.g. stopping a webcam that's already off)
        for command in (self._gopro.http_command.webcam_stop, self._gopro.http_command.webcam_exit):
            try:
                await command()
            except Exception:
                pass

    async def _wait_ready(self, timeout: float = 10.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = (await self._gopro.http_command.get_camera_state()).data
            if state.get(StatusId.READY) and not state.get(StatusId.BUSY):
                return
            await asyncio.sleep(0.5)
        raise RuntimeError(f"camera not ready / still busy after {timeout:.0f}s")

    async def start(self) -> None:
        for attempt in range(1, START_ATTEMPTS + 1):
            try:
                await self._reset()
                await self._wait_ready()
                # Take control from the on-camera UI (e.g. an open menu)
                await self._gopro.http_command.set_camera_control(mode=CameraControl.EXTERNAL)
                response = await self._gopro.http_command.webcam_start(
                    resolution=self._resolution,
                    fov=self._fov,
                    port=STREAM_PORT,
                    protocol=WebcamProtocol.TS,
                )
                if response.data.error != WebcamError.SUCCESS:
                    raise RuntimeError(f"webcam_start returned {response.data.error}")
                print(f"Webcam started: {self._resolution.name}, {self._fov.name}, udp port {STREAM_PORT}")
                return
            except Exception as e:
                print(f"Webcam start attempt {attempt}/{START_ATTEMPTS} failed: {e}")
                await asyncio.sleep(2)
        raise RuntimeError(f"Couldn't start webcam after {START_ATTEMPTS} attempts, try power cycling the camera")

    async def restart(self, reason: str) -> None:
        """Restart the webcam. Callers arriving mid-restart wait for it rather than restarting again."""
        if self._lock.locked():
            async with self._lock:
                return
        async with self._lock:
            self.restarts += 1
            print(f"[{time.strftime('%H:%M:%S')}] Restarting webcam ({reason}), restart #{self.restarts}")
            await self.start()

    @property
    def restarting(self) -> bool:
        return self._lock.locked()

    async def status(self) -> WebcamStatus | None:
        return (await self._gopro.http_command.webcam_status()).data.status

    async def stop(self) -> None:
        # Run every step even if one fails, so the camera isn't left in webcam mode
        steps = (
            ("webcam_stop", self._gopro.http_command.webcam_stop),
            ("webcam_exit", self._gopro.http_command.webcam_exit),
            ("release camera control", lambda: self._gopro.http_command.set_camera_control(mode=CameraControl.IDLE)),
        )
        for name, command in steps:
            try:
                await command()
            except Exception as e:
                print(f"Warning: {name} failed during shutdown: {e!r}")
        print("Webcam stopped")


async def monitor_state(
    webcam: Webcam, gopro: WiredGoPro, interval: float, sender: TelemetrySender, health: LinkHealth
) -> None:
    """Each interval: poll camera state, print temperature flags and battery, send
    telemetry, and restart the webcam if the camera says it stopped streaming.

    Restarts run as a separate task so telemetry keeps flowing (reporting
    "restarting") while one is in progress.
    """
    bad_polls = 0
    restart_task: asyncio.Task | None = None
    try:
        while True:
            if restart_task is not None and restart_task.done():
                restart_task.result()  # re-raise a failed restart, ending the task so main() shuts down
                restart_task = None

            try:
                state = (await gopro.http_command.get_camera_state()).data
                health.webcam_status = await webcam.status()
            except Exception as e:  # keep polling through transient HTTP errors
                health.poll_failures += 1
                health.webcam_status = None  # unknown, don't report the stale value
                print(f"[{time.strftime('%H:%M:%S')}] state poll failed: {e!r}")
                sender.send(None, health.as_json(webcam))
                await asyncio.sleep(interval)
                continue
            health.poll_failures = 0
            health.last_contact = time.monotonic()

            connection = health.as_json(webcam)
            sender.send(state, connection)
            hot = state.get(StatusId.OVERHEATING)
            cold = state.get(StatusId.COLD)
            battery = state.get(StatusId.INTERNAL_BATTERY_PERCENTAGE)
            warning = (" !! OVERHEATING" if hot else "") + (" !! TOO COLD" if cold else "")
            print(
                f"[{time.strftime('%H:%M:%S')}] overheating={hot} cold={cold} battery={battery}% "
                f"webcam={connection['webcam_status']} link={connection['state']}{warning}"
            )

            # The webcam is legitimately off mid-restart, so only count bad polls outside one
            streaming = health.webcam_status in (WebcamStatus.HIGH_POWER_PREVIEW, WebcamStatus.LOW_POWER_PREVIEW)
            bad_polls = 0 if streaming or webcam.restarting else bad_polls + 1
            if bad_polls >= BAD_STATUS_POLLS and restart_task is None:
                reason = f"camera reports webcam {connection['webcam_status']}"
                restart_task = asyncio.create_task(webcam.restart(reason))
                bad_polls = 0
            await asyncio.sleep(interval)
    finally:
        if restart_task is not None:
            restart_task.cancel()


class FrameReader:
    """Reads frames on a background thread, keeping only the newest one.

    cap.read() blocks until the next frame arrives, and reading slower than the
    camera sends lets ffmpeg's buffer fill up, adding latency. Draining it on a
    thread keeps the displayed frame current.
    """

    def __init__(self, url: str) -> None:
        # Timeouts so opening / reading a dead stream fails instead of hanging forever
        self._cap = cv.VideoCapture(
            url,
            cv.CAP_FFMPEG,
            [cv.CAP_PROP_OPEN_TIMEOUT_MSEC, 10000, cv.CAP_PROP_READ_TIMEOUT_MSEC, 5000],
        )
        if not self._cap.isOpened():
            self._cap.release()
            raise RuntimeError("couldn't open stream (no data within 10s)")
        self._frame = None
        self.frame_count = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            ok, frame = self._cap.read()
            if ok:
                with self._lock:
                    self._frame = frame
                    self.frame_count += 1

    def latest(self):
        with self._lock:
            return self._frame

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)
        self._cap.release()


def bind_udp(port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("0.0.0.0", port))
    except OSError as e:
        sock.close()
        if e.errno == errno.EADDRINUSE:
            raise RuntimeError(
                f"UDP port {port} is already in use, is another go_pro.py, nc or ffmpeg running? "
                f"(check with: ss -ulpn | grep {port})"
            ) from e
        raise
    return sock


def ensure_udp_port_free(port: int) -> None:
    bind_udp(port).close()


class UdpRelay:
    """Forwards the camera's UDP stream to a local port, counting bytes received.

    OpenCV doesn't expose how many bytes it receives, so the relay measures the
    stream's network bitrate (video plus a few % of MPEG-TS overhead).
    """

    def __init__(self, listen_port: int, forward_port: int) -> None:
        self._rx = bind_udp(listen_port)
        self._rx.settimeout(0.5)  # so the thread notices stop requests
        self._tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._forward = ("127.0.0.1", forward_port)
        self.byte_count = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                data = self._rx.recv(65535)
            except TimeoutError:
                continue
            self.byte_count += len(data)
            self._tx.sendto(data, self._forward)

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)
        self._rx.close()
        self._tx.close()


async def display_stream(webcam: Webcam, health: LinkHealth) -> None:
    """Show the stream until q / Esc is pressed or the window is closed.

    If no stream bytes arrive for STALL_SECONDS, restart the webcam and reopen the reader.
    """
    print("Opening stream (q or Esc to quit)...")
    # Relay must be running before OpenCV opens, as opening blocks until data arrives.
    # It stays up across webcam restarts; only the reader is reopened.
    relay = UdpRelay(STREAM_PORT, DECODE_PORT)
    reader: FrameReader | None = None
    window = "GoPro"
    shown = False
    # Measured receive rates, to confirm what the camera actually sends
    rate_text = ""
    last_count, last_bytes, last_time = 0, 0, time.monotonic()
    stall_bytes, last_data_time = 0, time.monotonic()
    try:
        while True:
            if reader is None:
                try:
                    reader = await asyncio.to_thread(FrameReader, DECODE_URL)
                    last_count, last_bytes, last_time = 0, relay.byte_count, time.monotonic()
                except RuntimeError as e:
                    print(f"Stream reader: {e}")  # the stall check below restarts the webcam

            now = time.monotonic()
            if relay.byte_count != stall_bytes:
                stall_bytes, last_data_time = relay.byte_count, now
                health.last_stream_data = now
            elif now - last_data_time > STALL_SECONDS:
                if reader is not None:
                    await asyncio.to_thread(reader.close)
                    reader = None
                await webcam.restart(f"no stream data for {STALL_SECONDS:.0f}s")
                last_data_time = time.monotonic()
                continue

            if now - last_time >= 1.0 and reader is not None:
                elapsed = now - last_time
                count, byte_count = reader.frame_count, relay.byte_count
                fps = (count - last_count) / elapsed
                mbps = (byte_count - last_bytes) * 8 / elapsed / 1e6
                rate_text = f"{fps:.1f} fps {mbps:.2f} Mbps"
                last_count, last_bytes, last_time = count, byte_count, now
            frame = reader.latest() if reader is not None else None
            if frame is not None:
                h, w = frame.shape[:2]
                frame = frame.copy()
                cv.putText(frame, f"{w}x{h} {rate_text}", (10, 30), cv.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
                cv.imshow(window, frame)
                shown = True
            key = cv.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
            if shown and cv.getWindowProperty(window, cv.WND_PROP_VISIBLE) < 1:
                break
            # Yield so open-gopro's background tasks (status polling) keep running
            await asyncio.sleep(0.005)
    finally:
        if reader is not None:
            reader.close()
        relay.close()
        cv.destroyAllWindows()


async def wait_for_enter() -> None:
    # A daemon thread rather than asyncio.to_thread: an executor thread blocked in
    # input() would keep the process alive after Ctrl-C / SIGTERM until Enter
    loop = asyncio.get_running_loop()
    pressed = asyncio.Event()
    threading.Thread(target=lambda: (input(), loop.call_soon_threadsafe(pressed.set)), daemon=True).start()
    await pressed.wait()


async def main(args: argparse.Namespace):
    # SIGTERM / SIGHUP (kill, closed terminal) cancel main like Ctrl-C does, so the
    # finally blocks below still take the camera out of webcam mode
    loop = asyncio.get_running_loop()
    main_task = asyncio.current_task()
    for sig in (signal.SIGTERM, signal.SIGHUP):
        loop.add_signal_handler(sig, main_task.cancel)

    if args.show:
        # Fail before touching the camera if a stale process holds the stream ports
        ensure_udp_port_free(STREAM_PORT)
        ensure_udp_port_free(DECODE_PORT)

    async with WiredGoPro() as gopro:
        while not await gopro.is_ready:
            await asyncio.sleep(0.1)
        print("Connected via USB and ready.")

        version = (await gopro.http_command.get_open_gopro_api_version()).data
        print(f"Open GoPro API version {version}")

        state = (await gopro.http_command.get_camera_state()).data
        print_section("Video", state, VIDEO_SETTINGS)
        print_section("Sensor / Lens", state, SENSOR_SETTINGS)
        print_section("Telemetry", state, STATUSES)

        webcam = Webcam(gopro, RESOLUTIONS[args.res], FOVS[args.fov])
        sender = TelemetrySender(
            args.telemetry_port,
            {"resolution": args.res, "fov": args.fov, "protocol": "TS", "port": STREAM_PORT},
        )
        health = LinkHealth()
        monitor = ui = None
        try:
            await webcam.start()
            print(f"Sending JSON telemetry to udp://127.0.0.1:{args.telemetry_port} every {args.poll}s")
            monitor = asyncio.create_task(monitor_state(webcam, gopro, args.poll, sender, health))
            if args.show:
                ui = asyncio.create_task(display_stream(webcam, health))
            else:
                print(f"Streaming to udp://@:{STREAM_PORT}, press Enter to stop")
                ui = asyncio.create_task(wait_for_enter())
            # The monitor only finishes by raising (a failed webcam restart); either way, stop
            done, _ = await asyncio.wait({ui, monitor}, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()  # re-raise any error
        finally:
            # Cancel and await the tasks so the display's own cleanup (window,
            # relay sockets) finishes before the webcam is stopped
            tasks = [t for t in (ui, monitor) if t is not None]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            sender.close()
            await webcam.stop()


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="GoPro HERO11 config readout and webcam stream over USB.")
    parser.add_argument("--res", choices=RESOLUTIONS, default="1080", help="webcam resolution (default 1080)")
    parser.add_argument("--fov", choices=FOVS, default="wide", help="webcam field of view (default wide)")
    parser.add_argument("--show", action="store_true", help="display the stream in an OpenCV window")
    parser.add_argument("--poll", type=float, default=5.0, help="camera state poll interval in seconds (default 5)")
    parser.add_argument("--telemetry-port", type=int, default=9595, help="localhost UDP port for JSON telemetry")
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(main(parse_arguments()))
