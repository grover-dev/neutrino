/*
 * Driver for the victron 100/30 MPPT
 */

/* TODO: Stream strings from file -> feed into parser state machine -> extract values -> write to a blackboard*/

/**
 * PID     0xA076
 * FW      174
 * SER#    HQ26097UUZA
 * V       11870
 * I       0
 * VPV     10
 * PPV     0
 * CS      0
 * MPPT    0
 * OR      0x00000001
 * ERR     0
 * LOAD    ON
 * H19     0
 * H20     0
 * H21     0
 * H22     0
 * H23     0
 * HSDS    0
 * Checksum
 */
use serialport::{ClearBuffer, SerialPort};
use std::io::Read;
use std::time::Duration;

#[derive(Default, Debug)]
pub struct VictronData {
    voltage_v: f32,
    current_a: f32,
    power_w: f32,
    load_on: bool,
    // FIXME: add errors, decode the H flags...
}

pub struct VictronMppt {
    port: Box<dyn SerialPort>,
    buffer: [u8; Self::BUFFER_SIZE],
    data: VictronData,
}

pub enum BaudRate {
    _19200,
}

impl VictronMppt {
    const BUFFER_SIZE: usize = 4096;

    pub fn new(portname: &str, baud_rate: BaudRate) -> Self {
        // let file = File::open(path)?;
        let baud_rate_int;
        match baud_rate {
            BaudRate::_19200 => baud_rate_int = 19_200,
        }

        // let me have my fucking nonblocking ports damn it
        let port = serialport::new(portname, baud_rate_int)
            .timeout(Duration::from_millis(10))
            .open()
            // FIXME: tbd...
            .expect("Failed to open port");

        // Flush after open
        port.clear(ClearBuffer::Input);

        // This fucking ticks me off
        let buffer = [0; Self::BUFFER_SIZE];
        let data = VictronData::default();
        Self {
            port: port,
            buffer: buffer,
            data: data,
        }
    }

    pub fn poll(&mut self) -> Option<VictronData> {
        /* Read from the serial port -> feed parser, tbd if line by line... simplest option is to wait for starter line (assume minimal corruption) */
        /* Create a key - type parser -> "string" : enum for hex/int/uint/f32? */
        /* - can keep a verbose debug pass through if reuqired (a la cmg rad test terminal) */

        if let Ok(bytes_read) = self.port.read(&mut self.buffer) {
            if bytes_read <= 0 {
                return None;
            }

            /* Feed the machine */
        }

        return None;
    }

    // fn the_machine()
}
