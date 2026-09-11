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
struct Data {
    voltage_v: float,
    current_a: float,
    power_w: float,
    load_on: bool,
    // FIXME: add errors, decode the H flags...
}

use std::fs::File;
use std::io::prelude::*;

use serialport::SerialPort;
struct VictronMppt {
    // file: File,
}

enum BaudRate
{
    _19200,
}

impl VictronMppt {
    fn new(portname: &str, baud_rate) -> io::Result<Self> {
        let file = File::open(path)?;
    }

    fn read()
    {

    }
}
