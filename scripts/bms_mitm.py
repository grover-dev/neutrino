#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["bleak>=0.22", "bless>=0.2.6"]
# ///
"""BLE man-in-the-middle relay for the Dyness 12V BMS.

The laptop advertises as the battery (same name, same GATT layout). The phone app
connects to the laptop; every write the app makes is forwarded to the real battery
and every notification from the battery is forwarded back to the app. Both
directions are logged with timestamps so the login/poll sequence can be replayed
from bms_probe.py.

Usage:
    uv run scripts/bms_mitm.py [logfile]

Setup:
  * Battery should be far enough away (or shielded) that the phone finds the laptop
    first, but still in range of the laptop. Or just try it: the phone shows two
    devices with the same name; pick the one that isn't the battery's MAC.
  * Advertising is created with `sudo btmgmt add-adv` (bluetoothd's own advertising
    fails on this laptop), so expect a sudo prompt.
  * Ctrl-C to stop. The advert is removed and the adapter alias restored on exit.
"""

import asyncio
import logging
import subprocess
import sys
import time
from datetime import datetime

from bleak import BleakClient
from bless import (
    BlessGATTCharacteristic,
    BlessServer,
    GATTAttributePermissions,
    GATTCharacteristicProperties,
)

BATTERY_MAC = "53:EE:10:0C:35:37"
BATTERY_NAME = "B7522604160102"
ADV_INSTANCE = 1  # btmgmt advertising instance id

PIPE_SVC = "00002760-08c2-11e1-9073-0e8ac72e1001"
PIPE_TX = "00002760-08c2-11e1-9073-0e8ac72e0001"  # app writes here
PIPE_RX = "00002760-08c2-11e1-9073-0e8ac72e0002"  # battery notifies here

# Device Information service, mirrored so the app sees what it expects. Disabled:
# on this stack only the first registered service is served, and the pipe matters
# more. Set True only to test whether the app needs these values.
MIRROR_DIS = False
DIS_SVC = "0000180a-0000-1000-8000-00805f9b34fb"
DIS_VALUES = {
    "00002a29-0000-1000-8000-00805f9b34fb": b"Nations",
    "00002a24-0000-1000-8000-00805f9b34fb": b"NS-BLE-1.0",
    "00002a27-0000-1000-8000-00805f9b34fb": b"1.0.0",
    "00002a26-0000-1000-8000-00805f9b34fb": b"1.0.0",
    "00002a28-0000-1000-8000-00805f9b34fb": b"1.0.0",
    "00002a25-0000-1000-8000-00805f9b34fb": b"1.0.0.0-LE",
    "00002a23-0000-1000-8000-00805f9b34fb": bytes.fromhex("123456FFFE9ABCDE"),
    "00002a2a-0000-1000-8000-00805f9b34fb": bytes.fromhex("FFEEDDCCBBAA"),
    "00002a50-0000-1000-8000-00805f9b34fb": bytes.fromhex("025E04400000" "03"),
}


def hexdump(data: bytes) -> str:
    return " ".join(f"{b:02X}" for b in data)


class Relay:
    def __init__(self, logfile: str):
        self.log = open(logfile, "a", buffering=1)  # noqa: SIM115
        self.t0 = time.monotonic()
        self.loop = asyncio.get_running_loop()
        self.battery: BleakClient | None = None
        self.server: BlessServer | None = None
        self.advertising = False

    def emit(self, direction: str, data: bytes):
        shown = hexdump(data)
        if direction.startswith("APP SUB") or direction.startswith("APP UNSUB"):
            shown = data.decode("utf-8", "replace")
        line = f"{time.monotonic() - self.t0:9.3f} {direction} ({len(data):3}) {shown}"
        print(line)
        self.log.write(line + "\n")

    # --- battery side (bleak central) -------------------------------------
    async def connect_battery(self):
        print(f"connecting to battery {BATTERY_MAC} ...")
        self.battery = BleakClient(BATTERY_MAC, disconnected_callback=self._battery_lost)
        await self.battery.connect()
        await self.battery.start_notify(PIPE_RX, self._on_battery_notify)
        print("battery connected")

    def _battery_lost(self, _client):
        print("!! battery disconnected; reconnecting")
        self.loop.create_task(self._reconnect_battery())

    async def _reconnect_battery(self):
        await asyncio.sleep(1)
        for _ in range(10):
            try:
                await self.connect_battery()
                return
            except Exception as e:  # noqa: BLE001
                print(f"   reconnect failed: {e}")
                await asyncio.sleep(2)

    def _on_battery_notify(self, _sender, data: bytearray):
        data = bytes(data)
        self.emit("BAT->APP", data)
        # forward to the phone
        ch = self.server.get_characteristic(PIPE_RX)
        ch.value = bytearray(data)
        self.server.update_value(PIPE_SVC, PIPE_RX)

    # --- phone side (bless peripheral) ------------------------------------
    def _on_app_read(self, ch: BlessGATTCharacteristic, **_):
        self.emit("APP READ", bytes(ch.value or b""))
        return ch.value

    def _on_app_write(self, ch: BlessGATTCharacteristic, value: bytes, **_):
        data = bytes(value)
        ch.value = bytearray(data)
        if ch.uuid.lower() != PIPE_TX:
            self.emit(f"APP WRITE {ch.uuid[4:8]}", data)
            return
        self.emit("APP->BAT", data)
        self.loop.create_task(self._forward_to_battery(data))

    async def _forward_to_battery(self, data: bytes):
        if not self.battery or not self.battery.is_connected:
            print("!! battery not connected, dropping write")
            return
        try:
            await self.battery.write_gatt_char(PIPE_TX, data, response=False)
        except Exception as e:  # noqa: BLE001
            print(f"!! forward failed: {e}")

    async def start_server(self):
        self.server = BlessServer(name=BATTERY_NAME, loop=self.loop)
        self.server.read_request_func = self._on_app_read
        self.server.write_request_func = self._on_app_write

        # Only the FIRST service added actually reaches the served GATT database
        # on this stack (verified with scripts/parse_snoop.py: a second service is
        # silently dropped), so register the pipe and nothing else. Advertising is
        # done separately via btmgmt, so we don't need a 16-bit UUID service here.
        await self.server.add_new_service(PIPE_SVC)
        await self.server.add_new_characteristic(
            PIPE_SVC, PIPE_TX,
            GATTCharacteristicProperties.write_without_response,
            None,
            GATTAttributePermissions.writeable,
        )
        await self.server.add_new_characteristic(
            PIPE_SVC, PIPE_RX,
            GATTCharacteristicProperties.notify,
            None,
            GATTAttributePermissions.readable,
        )
        if MIRROR_DIS:
            await self.server.add_new_service(DIS_SVC)
            for uuid, val in DIS_VALUES.items():
                await self.server.add_new_characteristic(
                    DIS_SVC, uuid,
                    GATTCharacteristicProperties.read,
                    bytearray(val),
                    GATTAttributePermissions.readable,
                )

        # Register the GATT server with bluetoothd, but do NOT use bless/bluetoothd
        # for advertising: bluetoothd 5.85 + this Qualcomm controller fails with
        # "Invalid Parameters", while a direct kernel request via btmgmt works.
        await self.server.setup_task

        # bless stubs these out; hook them so we can see whether the phone ever
        # finds and subscribes to our notify characteristic. No subscribe =
        # the app never discovered the pipe service.
        def _start_notify(ch):
            self.emit("APP SUBSCRIBE", f"{getattr(ch, 'UUID', ch)}".encode())

        def _stop_notify(ch):
            self.emit("APP UNSUBSCRIBE", f"{getattr(ch, 'UUID', ch)}".encode())

        self.server.app.StartNotify = _start_notify
        self.server.app.StopNotify = _stop_notify

        await self.server.app.set_name(self.server.adapter, BATTERY_NAME)
        self.server.bus.export(self.server.app.path, self.server.app)
        await self.server.app.register(self.server.adapter)
        print("GATT server registered")

        # -c connectable, -g general discoverable, -n include local name (adapter
        # alias, set above), -u 180a mirrors the real battery's advert.
        r = subprocess.run(
            ["sudo", "btmgmt", "add-adv", "-c", "-g", "-n", "-u", "180a", str(ADV_INSTANCE)],
            capture_output=True, text=True,
        )
        if "Instance added" not in r.stdout:
            raise RuntimeError(f"btmgmt add-adv failed: {r.stdout} {r.stderr}")
        self.advertising = True
        print(f"advertising as {BATTERY_NAME!r}; connect the app to the laptop now")

    async def stop_server(self):
        if self.advertising:
            subprocess.run(["sudo", "btmgmt", "rm-adv", str(ADV_INSTANCE)], capture_output=True)
        if self.server:
            try:
                await self.server.app.unregister(self.server.adapter)
                self.server.bus.unexport(self.server.app.path, self.server.app)
            except Exception as e:  # noqa: BLE001
                print(f"(gatt unregister: {e})")


def adapter_alias() -> str:
    try:
        out = subprocess.run(["bluetoothctl", "show"], capture_output=True, text=True, timeout=5).stdout
        for line in out.splitlines():
            if line.strip().startswith("Alias:"):
                return line.split(":", 1)[1].strip()
    except Exception:  # noqa: BLE001
        pass
    return ""


async def main(logfile: str):
    old_alias = adapter_alias()
    relay = Relay(logfile)
    try:
        # Advertise first: some controllers refuse to start advertising while
        # they already hold a central connection.
        await relay.start_server()
        await relay.connect_battery()
        while True:
            await asyncio.sleep(3600)
    finally:
        await relay.stop_server()
        if relay.battery and relay.battery.is_connected:
            try:
                await relay.battery.disconnect()
            except Exception as e:  # noqa: BLE001
                print(f"(battery disconnect: {e})")
        if old_alias:
            subprocess.run(["bluetoothctl", "system-alias", old_alias], capture_output=True)
        relay.log.close()
        print(f"log written to {logfile}")


if __name__ == "__main__":
    # bleak's D-Bus watcher logs a KeyError for our own GATT server objects; harmless.
    logging.getLogger("dbus_fast").setLevel(logging.CRITICAL)
    logging.getLogger("bleak").setLevel(logging.CRITICAL)
    log = sys.argv[1] if len(sys.argv) > 1 else f"mitm_{datetime.now():%Y%m%d_%H%M%S}.log"
    try:
        asyncio.run(main(log))
    except KeyboardInterrupt:
        pass
