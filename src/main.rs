use devices::pico_motor_control::{self, MotorControllerCommand, MotorControllerState};

use std::io::{self, BufRead, Write};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};
use std::thread;

const LOOP_PERIOD: Duration = Duration::from_millis(100); // 10 Hz
const TELEM_PRINT_TICKS: u32 = 10; // 1 Hz

/// Parse "MOTOR_A_DUTY_CYCLE, MOTOR_B_DUTY_CYCLE" into a command
fn parse_command(line: &str) -> Option<MotorControllerCommand> {
    let (a, b) = line.split_once(',')?;
    let motor_a_duty_cycle: f32 = a.trim().parse().ok()?;
    let motor_b_duty_cycle: f32 = b.trim().parse().ok()?;

    Some(MotorControllerCommand {
        motor_a_duty_cycle,
        motor_b_duty_cycle,
        // Only enable the motors if something is actually being commanded
        enable_motors_bool: (motor_a_duty_cycle != 0.0 || motor_b_duty_cycle != 0.0) as u32,
    })
}

fn main() {
    // Start with motors off
    let command = Arc::new(Mutex::new(MotorControllerCommand::default()));

    // Background thread: send the latest command to the motor controller at 10 Hz
    let loop_command = Arc::clone(&command);
    thread::spawn(move || {
        let mut motor = pico_motor_control::PicoMotorController::new("/dev/pico_motor_control");
        let mut next_tick = Instant::now();
        let mut last_telem: Option<MotorControllerState> = None;
        let mut tick: u32 = 0;
        loop {
            let current = *loop_command.lock().unwrap();
            motor.send_command(&current);

            // Read every cycle so the serial buffer doesn't back up, keep the latest
            if let Some(telem) = motor.read_telemetry() {
                last_telem = Some(telem);
            }

            // Print the latest telemetry once a second
            if tick % TELEM_PRINT_TICKS == 0 {
                match last_telem {
                    Some(telem) => println!("[telem] {:?}", telem),
                    None => println!("[telem] none received yet"),
                }
            }
            tick = tick.wrapping_add(1);

            next_tick += LOOP_PERIOD;
            let now = Instant::now();
            if next_tick > now {
                thread::sleep(next_tick - now);
            } else {
                // Fell behind, reset rather than bursting to catch up
                next_tick = now;
            }
        }
    });

    // Foreground: read new commands from stdin
    println!("Enter MOTOR_A_DUTY_CYCLE, MOTOR_B_DUTY_CYCLE (e.g. 0.5, -0.5). Ctrl-D to quit.");
    let stdin = io::stdin();
    loop {
        print!("> ");
        let _ = io::stdout().flush();

        let mut line = String::new();
        match stdin.lock().read_line(&mut line) {
            Ok(0) => break, // EOF
            Ok(_) => {}
            Err(e) => {
                eprintln!("Failed to read stdin: {}", e);
                break;
            }
        }

        match parse_command(&line) {
            Some(new_command) => {
                *command.lock().unwrap() = new_command;
                println!("Commanding {:?}", new_command);
            }
            None => println!("Expected: MOTOR_A_DUTY_CYCLE, MOTOR_B_DUTY_CYCLE"),
        }
    }

    // Stop the motors before exiting
    *command.lock().unwrap() = MotorControllerCommand::default();
    thread::sleep(LOOP_PERIOD * 2);
}
