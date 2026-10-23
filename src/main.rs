/*
 * fuck it we ball
 */
// #![no_std]
use bms::DynessBmsData;
use devices::gps::GpsData;
use devices::pico_digital_compass::{DigitalCompassCommand, DigitalCompassTelemetry};
use devices::pico_motor_control::{self, MotorControllerCommand, MotorControllerState};
use devices::vehicle_watchdog::{VehicleWatchdog, VehicleWatchdogTelem};
use devices::{bms, gps, pico_digital_compass};

use commands::{CommandRequest, CommandResponse, Resp};
use serde::Serialize;
use std::{net::UdpSocket, thread, time::Duration};

use std::time::Instant;

const WATCHDOG_TIMEOUT_SECS: u64 = 10;

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
    vehicle_watchdog: VehicleWatchdog,
    vehicle_watchdog_telem: VehicleWatchdogTelem,
}

use schema::Schema;
use std::net::Ipv4Addr;
use std::net::SocketAddr;
// How to make a trait that exposes a hash assignment + type extraction for each member?
// - probably doable? is it easier to just code gen it? tbd...
// - commands are packaged as json, field + value

// Create a fallback IP address (e.g., 0.0.0.0:0) outside or inside the loop

fn main() {
    // app monotonic time!
    let now = Instant::now();

    let mut gps = gps::Gps::new("/dev/gps");
    // FIXME: how to hard wire these? tbd...
    let mut digital_compass =
        pico_digital_compass::PicoDigitalCompass::new("/dev/pico_digital_compass");
    let mut motor = pico_motor_control::PicoMotorController::new("/dev/pico_motor_control");

    let mut bms: bms::DynessBms = bms::DynessBms::new("127.0.0.1:9000");
    // TODO:
    // - MPPT // Defer
    // - Starlink // Maybe defer? tbd...
    // - jetson data (cpu, temp, etc...) // Defer
    // - Power pico // Probably required
    // - Camera data // required

    // FIXME: Add some config or something for socket coordination...
    let telem_socket = UdpSocket::bind("127.0.0.1:34254").expect("socket failed to bind");
    telem_socket
        .connect("127.0.0.1:34254")
        .expect("socket failed to connect");

    let fallback_addr = SocketAddr::new(Ipv4Addr::UNSPECIFIED.into(), 0);
    let command_socket = UdpSocket::bind("0.0.0.0:34255").expect("socket failed to bind");
    command_socket
        .set_nonblocking(true)
        .expect("failed to mark socket as non blocking");

    let response_socket = UdpSocket::bind("127.0.0.1:34256").expect("socket failed to bind");
    response_socket
        .connect("127.0.0.1:34256")
        .expect("socket failed to connect");

    let mut is_first_run = 1;

    let mut blackboard = Blackboard::default();
    let mut buf = [0u8; 65536];
    loop {
        // Limit to 10 messages per cycle max
        for _ in 0..10 {
            let (number_of_bytes, _) = command_socket
                .recv_from(&mut buf)
                .unwrap_or((0, fallback_addr));

            if number_of_bytes == 0 {
                // No more messages
                break;
            }
            let mut_slice: &mut [u8] = &mut buf[0..number_of_bytes];

            let Ok(command): Result<CommandRequest, _> = serde_json::from_slice(mut_slice) else {
                // Failed to deserialize this message, skip and move on
                println!("Failed to deserialize the buffer into a CommandRequest, skipping");
                continue;
            };

            // Now match on the struct type...
            // This should eventually become a map look up but i cant be bothered rn...

            let result = match command.struct_hash {
                MotorControllerCommand::HASH => blackboard
                    .motor_command
                    .set_field(command.field_hash, command.value),
                DigitalCompassCommand::HASH => blackboard
                    .digital_compass_command
                    .set_field(command.field_hash, command.value),
                VehicleWatchdog::HASH => blackboard
                    .vehicle_watchdog
                    .set_field(command.field_hash, command.value),
                _ => Err(schema::SetError::StructMismatch),
            };

            let response = CommandResponse {
                command_id: command.command_id,
                response: if result.is_ok() {
                    Resp::Success
                } else {
                    Resp::Failure
                },
            };

            match serde_json::to_string(&response) {
                Ok(json_string) => {
                    let bytes: &[u8] = json_string.as_bytes();
                    let _ = response_socket.send(bytes);
                }
                Err(e) => eprintln!("Serialization failed: {}", e),
            }
        }

        // TODO: add command server or something?
        // TODO: Add some keep alive flag sent by a server -> if the keep alive dies we shut off the motors

        // read_datashare()
        // TODO: Future thing for more complex systems, probabl ynot necessayr here

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
        // If the timeout expires, force the motors off
        if blackboard.vehicle_watchdog_telem.watchdog_lockout_active > 0 {
            blackboard.motor_command = MotorControllerCommand {
                motor_a_duty_cycle: 0.0,
                motor_b_duty_cycle: 0.0,
                enable_motors_bool: 0,
            }
        }

        digital_compass.send_command(&blackboard.digital_compass_command);
        motor.send_command(&blackboard.motor_command);

        // send_telem()
        if blackboard.vehicle_watchdog.keep_alive > 0 {
            let current_time_secs = now.elapsed().as_secs();
            blackboard.vehicle_watchdog_telem.seconds_to_expiry = WATCHDOG_TIMEOUT_SECS;
            blackboard.vehicle_watchdog_telem.seconds_last_pet_time = current_time_secs;
            blackboard.vehicle_watchdog_telem.watchdog_lockout_active = 0;
        } else {
            let current_time_secs = now.elapsed().as_secs();
            let delta = current_time_secs - blackboard.vehicle_watchdog_telem.seconds_last_pet_time;

            if delta > WATCHDOG_TIMEOUT_SECS {
                blackboard.vehicle_watchdog_telem.seconds_to_expiry = 0;
            } else {
                blackboard.vehicle_watchdog_telem.seconds_to_expiry = WATCHDOG_TIMEOUT_SECS - delta;
            }
        }

        if blackboard.vehicle_watchdog_telem.seconds_to_expiry == 0 || is_first_run > 0 {
            blackboard.vehicle_watchdog_telem.watchdog_lockout_active = 1;
            is_first_run = 0;
        }

        blackboard.vehicle_watchdog.keep_alive = 0;

        send_telem(blackboard, &telem_socket);

        // FIXME: Not quite right butgood nuff for now
        thread::sleep(Duration::from_millis(100));
    }
}
