use bytemuck::Pod;
use serialport::{ClearBuffer, SerialPort};
/**
 * Interface to a pi pico, used to drive motors and monitor power consumption
 */
use slipspeed::{SlipReader, encode_frame};
use std::io::Cursor;
use std::marker::PhantomData;
use std::time::Duration;

/// Describes the wire protocol of a specific pico firmware
pub trait PicoDevice {
    type Telemetry: Pod;
    type Command: Pod;
}

pub struct PiPico<D: PicoDevice> {
    port: Box<dyn SerialPort>,
    _device: PhantomData<D>,
}

impl<D: PicoDevice> PiPico<D> {
    pub fn new(portname: &str) -> Self {
        let port = serialport::new(portname, 115_200)
            .timeout(Duration::from_millis(10))
            .open()
            // FIXME: tbd... eventually change this to do best effort sao that we dont crash if the mppt fails to open
            .expect("Failed to open port");

        // Flush after open, dont care about return
        let _ = port.clear(ClearBuffer::Input);
        Self {
            port: port,
            _device: PhantomData,
        }
    }

    pub fn send_command(&mut self, command: &D::Command) {
        /* Convert to bytes, slip encode, and send */
        let bytes: &[u8] = bytemuck::bytes_of(command);
        let frame = encode_frame(&bytes);
        let _ = self.port.write_all(&frame);
    }

    pub fn read_telemetry(&mut self) -> Option<D::Telemetry> {
        let mut buffer: [u8; 64] = [0; 64];
        let Ok(bytes_read) = self.port.read(&mut buffer) else {
            return None;
        };

        let mut reader = SlipReader::new(Cursor::new(&buffer[..bytes_read]));

        /* Read frames out, keeping the last one of the correct size */
        let mut latest = None;
        while let Ok(Some(frame)) = reader.read_frame() {
            if let Ok(state) = bytemuck::try_pod_read_unaligned::<D::Telemetry>(&frame) {
                latest = Some(state);
            }
        }
        return latest;
    }
}
