// FIXME: RENAME AND RESTRUCTURE TO BE MORE GENERIC -> STRUCT PASSED IN EXTERNALLY!

use serialport::{ClearBuffer, SerialPort};
/**
 * Interface to a pi pico, used to drive motors and monitor power consumption
 */
use slipspeed::{SlipReader, encode_frame};
use std::io::Cursor;
use std::mem::size_of;
use std::time::Duration;

// Has to match the format in pi_pico/pi_pico/structs.h
#[derive(Default, Debug, Clone, Copy, bytemuck::Pod, bytemuck::Zeroable)]
#[repr(C)] // Ensures a stable memory layout
pub struct PiPicoCommand {
    pub motor_a_duty_cycle: f32,
    pub motor_b_duty_cycle: f32,
}

#[derive(Default, Debug, Copy, Clone)]
#[repr(C)] // Ensures a stable memory layout
pub struct PiPicoState {
    commanded_motor_a_duty_cycle: f32,
    commanded_motor_b_duty_cycle: f32,
    motor_voltage_v: f32,
    motor_current_ma: f32,
}

pub struct PiPico {
    port: Box<dyn SerialPort>,
}

impl PiPico {
    pub fn new(portname: &str) -> Self {
        let port = serialport::new(portname, 115_200)
            .timeout(Duration::from_millis(10))
            .open()
            // FIXME: tbd... eventually change this to do best effort sao that we dont crash if the mppt fails to open
            .expect("Failed to open port");

        // Flush after open, dont care about return
        let _ = port.clear(ClearBuffer::Input);
        Self { port: port }
    }

    // FIXME: replace this with a struct in the future...
    pub fn command_duty_cycle(&mut self, command: &PiPicoCommand) {
        /* Convert to bytes, slip encode, and send */
        let bytes: &[u8] = bytemuck::bytes_of(command);
        let frame = encode_frame(&bytes);
        let _ = self.port.write_all(&frame);
    }

    pub fn poll(&mut self) -> Option<PiPicoState> {
        let mut buffer: [u8; 64] = [0; 64];
        let Ok(bytes_read) = self.port.read(&mut buffer) else {
            return None;
        };

        let mut reader = SlipReader::new(Cursor::new(&buffer[..bytes_read]));

        let mut final_state: PiPicoState = PiPicoState::default();
        let mut found: bool = false;
        while let Ok(Some(frame)) = reader.read_frame() {
            /* Read frames out until we get the correct size,  */
            if frame.len() == (size_of::<PiPicoState>()) {
                found = true;
                /* Rust let me be free jesus christ */
                unsafe {
                    final_state = *(frame.as_ptr() as *const PiPicoState);
                }
            }
        }

        if !found {
            return None;
        }
        return Some(final_state);
    }
}
