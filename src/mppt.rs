/*
 * Driver for the victron 100/30 MPPT
 */

/* TODO: Stream strings from file -> feed into parser state machine -> extract values -> write to a blackboard*/

use circular_buffer::FixedCircularBuffer;
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
use std::default;
use std::io::{self, BufRead, BufReader, Read};
use std::time::Duration;

#[derive(Default, Debug, Clone, Copy)]
pub struct VictronData {
    voltage_v: f32,
    current_a: f32,
    power_w: f32,
    load_on: bool,
    // FIXME: add errors, decode the H flags...
}

pub struct VictronMppt {
    // FIXME: replace this with a buffered read...
    port_reader: BufReader<Box<dyn SerialPort>>,

    // buffer: FixedCircularBuffer<Self::BUFFER_SIZE, u8>,
    // buffer: FixedCircularBuffer<u8, 1024>,
    data: VictronData,
    pid_found: bool,
}

// enum MemberVariable {
//     Voltage,
//     Current,
//     Uinteger,
//     Integer,
//     Float,
//     Bool,
//     Hex,
// }

// impl KeyTypeParse {
//     pub fn parse(parse_struct: &[KeyTypeParse]) {
//         /* FIXME: how to structure this... tbd... */
//     }
// }

pub enum BaudRate {
    _19200,
}

impl VictronMppt {
    // const BUFFER_SIZE: usize = 4096;

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

        /* Wrap the port in a buffered reader */
        let mut reader = BufReader::new(port);

        // This fucking ticks me off
        // let buffer = FixedCircularBuffer::<u8, 1024>::default();
        let data = VictronData::default();
        Self {
            port_reader: reader,
            // buffer: buffer,
            data: data,
            pid_found: false,
        }
    }

    pub fn poll(&mut self) -> Option<VictronData> {
        /* Read from the serial port -> feed parser, tbd if line by line... simplest option is to wait for starter line (assume minimal corruption) */
        /* Create a key - type parser -> "string" : enum for hex/int/uint/f32? */
        /* - can keep a verbose debug pass through if reuqired (a la cmg rad test terminal) */
        // struct KeyTypeParse {
        //     key: &'static str,
        //     var_type: VarType,
        // }

        // static KEYS: &[KeyTypeParse] = &[
        //     KeyTypeParse {
        //         key: "V",
        //         var_type: VarType::Integer,
        //     },
        //     KeyTypeParse {
        //         key: "I",
        //         var_type: VarType::Integer,
        //     },
        // ];

        // struct KeyTypeParse {
        //     key: &'static str,
        //     var_type: VarType,
        // }

        // static KEYS: &[&'static str] = &[
        //     "V",
        //     "I",
        //     // KeyTypeParse {
        //     //     key: "V",
        //     //     var_type: VarType::Integer,
        //     // },
        //     // KeyTypeParse {
        //     //     key: "I",
        //     //     var_type: VarType::Integer,
        //     // },
        // ];

        // let mut buffer: [u8; 1024] = [0; 1024];
        let mut line = String::new(); // <- make this a member to prevent full allocation/deallocation every cyle... maybe pre buffer

        // let mut bool message_started = false;
        if let Ok(bytes_read) = self.port_reader.read_line(&mut line) {
            if bytes_read <= 0 {
                return None;
            }

            if (line.trim_matches('\r').contains("PID")) {
                if (!self.pid_found) {
                    /* Start a new block */
                    self.pid_found = true;
                } else {
                    /* Return what we have in the buffer, even if its not filled out */
                    return Some(self.data);
                }
                // FIXME: If PID is found again before we extract the full message -> return parsed contents for consumption
            } else if (self.pid_found) {
                // for key_value in KEYS {
                let key: Option<&str> = line.split_whitespace().nth(0);

                if (key == None) {
                    return None;
                }

                let integer: i64 = key.unwrap().parse().ok().unwrap_or(0);
                let float: f32 = key.unwrap().parse().ok().unwrap_or(0.0);

                if (key == Some("V")) {
                    self.data.voltage_v = (integer as f32) / 100.0;
                } else if key == Some("A") {
                    // FIXME: double check the units here
                    self.data.current_a = (integer as f32);
                }
                // .any(|word| word == key_value.key);
                // let is_present: bool =
                //     line.split_whitespace().any(|word| word == key_value.key);

                // if (is_present) {
                //     /* FIXME: Huzzah! */
                //     /* How to map to a field tho... */
                // }
                // }
                /* Only reach here if not present: log! */
            } else {
                /* No PID found, what to do... */
                return None;
            }
            // FIXME: I now have a line!
            // expensive but wahtever, future problem
            // FIXME: is this even required?
            // for &byte in &buffer[..bytes_read] {
            //     self.buffer.push_back(byte);
            // }

            // FIXME: now we look for PID to anchor our next operation
            // - try to get a line (terminated by \n) from the circular buffer -> pop value from the buffer if found
            // - look for PID in test

            /* Feed the machine */
            // Need to scan for starter characters to align frame?
            // -> keep feeding state machine until it finds "PID"
            //    -> once PID is found we can start parsing line by line
            //    -> read out data from serial into the buffer
        }

        return None;
    }

    // fn the_machine()
}
