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

// FIXME: Turn this into a class of some variety? tbd...
#[derive(Default, Debug, Copy, Clone, Serialize)]
struct Blackboard {
    motor_telem: MotorControllerState,
    motor_command: MotorControllerCommand,
    digital_compass_telem: DigitalCompassTelemetry,
    digital_compass_command: DigitalCompassCommand,
    gps_telem: GpsData,
}

fn main() {
    let mut gps = gps::Gps::new("/dev/ttyUSB0");
    // FIXME: how to hard wire these? tbd...
    let mut digital_compass = pico_digital_compass::PicoDigitalCompass::new("/dev/ttyACM0");
    let mut motor = pico_motor_control::PicoMotorController::new("/dev/ttyACM1");

    // FIXME: double check these...
    let socket = UdpSocket::bind("127.0.0.1:34254").expect("socket failed to bind");
    socket
        .connect("127.0.0.1:34254")
        .expect("socket failed to connect");

    let mut blackboard = Blackboard::default();
    loop {
        // read_commands()
        // TODO: add command server of some variety?

        // read()
        let gps_data = gps.update();
        let compass_data = digital_compass.read_telemetry();
        let motor_data = motor.read_telemetry();

        if let Ok(data) = gps_data {
            blackboard.gps_telem = data;
        }

        if let Some(data) = compass_data {
            blackboard.digital_compass_telem = data;
        }

        if let Some(data) = motor_data {
            blackboard.motor_telem = data;
        }

        // step()
        // TODO: RUN MATH HERE!

        // write()
        motor.send_command(&blackboard.motor_command);

        // send_telem()

        send_telem(blackboard, &socket);

        // FIXME: Not quite right butgood nuff for now
        thread::sleep(Duration::from_millis(100));
    }
}
