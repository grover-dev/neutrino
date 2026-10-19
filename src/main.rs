/*
 * fuck it we ball
 */
// #![no_std]
use bms::DynessBmsData;
use devices::gps::GpsData;
use devices::pico_digital_compass::{DigitalCompassCommand, DigitalCompassTelemetry};
use devices::pico_motor_control::{self, MotorControllerCommand, MotorControllerState};
use devices::{bms, gps, pi_pico, pico_digital_compass};
use gps::GpsError;

use chrono::{DateTime, Utc};
use db::{Database, Measurement, Record};
use serde::Serialize;
use std::process::Command;
use std::{net::UdpSocket, thread, time::Duration};

// FIXME: split this off into a separate file eventually
// i want to create a trait that will append a timestamp field + add a to json method for any given struct?
// - or this could be a free floating function, tbd... former if telemetry becomes a special type

fn send_telem<T: Serialize>(data: T, socket: &UdpSocket) {
    // first: convert to json
    // second: send over udp

    match serde_json::to_string(&data) {
        Ok(json_string) => {
            let bytes: &[u8] = json_string.as_bytes();
            let _ = socket.send(bytes);
        }
        Err(e) => eprintln!("Serialization failed: {}", e),
    }
}

// FIXME: Look at building a generator for blackboards + solutions for telemetry and commanding, pretty clean pattern
#[derive(Default, Debug, Copy, Clone, Serialize)]
struct Blackboard {
    motor_telem: MotorControllerState,
    motor_command: MotorControllerCommand,
    digital_compass_telem: DigitalCompassTelemetry,
    digital_compass_command: DigitalCompassCommand,
    gps_telem: GpsData,
    bms_telem: DynessBmsData,
}

fn main() {
    let mut gps = gps::Gps::new("/dev/ttyUSB0");
    // FIXME: how to hard wire these? tbd...
    let mut digital_compass = pico_digital_compass::PicoDigitalCompass::new("/dev/ttyACM0");
    let mut motor = pico_motor_control::PicoMotorController::new("/dev/ttyACM1");

    let mut bms: bms::DynessBms = bms::DynessBms::new("127.0.0.1:9000");
    // TODO:
    // - MPPT
    // - Starlink
    // - jetson data (cpu, temp, etc...)
    // - Power pico
    // - Camera data

    // FIXME: double check these...
    let socket = UdpSocket::bind("127.0.0.1:34254").expect("socket failed to bind");
    socket
        .connect("127.0.0.1:34254")
        .expect("socket failed to connect");

    let mut blackboard = Blackboard::default();
    loop {
        // read_commands()
        // TODO: add command server or something?
        // TODO: Add some keep alive flag sent by a server -> if the keep alive dies we shut off the motors

        // read()
        let gps_data = gps.update();
        let compass_data = digital_compass.read_telemetry();
        let motor_data = motor.read_telemetry();

        let bms_data = bms.poll();

        if let Ok(data) = gps_data {
            blackboard.gps_telem = data;
        }

        if let Some(data) = compass_data {
            blackboard.digital_compass_telem = data;
        }

        if let Some(data) = motor_data {
            blackboard.motor_telem = data;
        }

        if let Some(data) = bms_data {
            blackboard.bms_telem = data;
        }

        // step()
        // TODO: RUN MATH HERE!

        // write()
        digital_compass.send_command(&blackboard.digital_compass_command);
        motor.send_command(&blackboard.motor_command);

        // send_telem()

        send_telem(blackboard, &socket);

        // FIXME: Not quite right butgood nuff for now
        thread::sleep(Duration::from_millis(100));
    }
}
