use bytemuck::NoUninit;
use serialport::{ClearBuffer, SerialPort};
/**
 * Interface to a pi pico, used to drive motors and monitor power consumption
 */
use slipspeed::{SlipReader, encode_frame};
use std::io::Cursor;
use std::mem::size_of;
use std::time::Duration;

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
    pub fn command_duty_cycle<T: NoUninit>(&mut self, command: &T) {
        /* Convert to bytes, slip encode, and send */
        let bytes: &[u8] = bytemuck::bytes_of(command);
        let frame = encode_frame(&bytes);
        let _ = self.port.write_all(&frame);
    }

    pub fn poll<T: Default + Copy>(&mut self) -> Option<T> {
        let mut buffer: [u8; 64] = [0; 64];
        let Ok(bytes_read) = self.port.read(&mut buffer) else {
            return None;
        };

        let mut reader = SlipReader::new(Cursor::new(&buffer[..bytes_read]));

        let mut final_state = T::default();
        let mut found: bool = false;
        while let Ok(Some(frame)) = reader.read_frame() {
            /* Read frames out until we get the correct size,  */
            if frame.len() == (size_of::<T>()) {
                found = true;
                /* Rust let me be free jesus christ */
                unsafe {
                    final_state = *(frame.as_ptr() as *const T);
                }
            }
        }

        if !found {
            return None;
        }
        return Some(final_state);
    }
}
