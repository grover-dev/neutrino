/**
 * Driver for the Dyness 12V Smart Battery
 * Im cheap so i picked the cheap battery with the bluetooth BMS, thus we now do jank shit to deal with it
 */
/*
 * TODO: Connect to bluetooth (allow for retries?) -> send command to read data -> parse response
 */
use serde::Deserialize;
use std::net::UdpSocket;

/// One reading as pushed by scripts/bms_client.py, newline-delimited JSON over
/// TCP (this app listens, the script connects out, so startup order doesn't
/// matter). Schema is flat and fixed: no arrays, no nulls.
///
/// `mosfet_temp_c` / `aux_temp_c` are 0.0 when no sensor is fitted, which is
/// indistinguishable from a real 0 C reading. `fault_code` is the low byte of
/// register 0x000B and `status_flags` the high byte, of which only bit 2
/// (charge MOSFET) and bit 3 (discharge MOSFET) are known.
///
/// See BMS_README.md for the register map.
#[derive(Deserialize, Default, Debug, Clone)]
pub struct DynessBmsData {
    pub seq: u64,
    pub ts: String,

    pub voltage_v: f64,
    pub current_a: f64,
    pub power_w: f64,

    pub soc_pct: u16,
    pub soh_pct: u16,
    pub remaining_ah: f64,
    pub full_capacity_ah: f64,
    pub design_capacity_ah: f64,
    pub cycles: u16,

    pub alarm: u16,
    pub protection: u16,
    pub fault_code: u8,
    pub status_flags: u8,
    pub charge_mosfet: bool,
    pub discharge_mosfet: bool,

    pub cell_1_v: f64,
    pub cell_2_v: f64,
    pub cell_3_v: f64,
    pub cell_4_v: f64,
    pub cell_min_v: f64,
    pub cell_max_v: f64,
    pub cell_delta_mv: f64,

    pub temp_1_c: f64,
    pub mosfet_temp_c: f64,
    pub aux_temp_c: f64,
}

pub struct DynessBms {
    // FIXME: Need the bluetooth object
    socket: std::net::UdpSocket,
    buffer: [u8; 1500],
}

impl DynessBms {
    pub fn new(addr: &str) -> Self {
        let socket = UdpSocket::bind(addr).expect("Failed to open socket");

        Self {
            socket: socket,
            buffer: [0; 1500],
        }
    }

    pub fn poll(&mut self) -> Option<DynessBmsData> {
        /* This shit is heinous what the fuck rust */
        let Ok((amt, _)) = self.socket.recv_from(&mut self.buffer) else {
            return None;
        };

        /* Extract as utf8 string */
        let Ok(json_str) = std::str::from_utf8(&self.buffer[..amt]) else {
            return None;
        };

        let Ok(data): Result<DynessBmsData, _> = serde_json::from_str(json_str) else {
            return None;
        };

        return Some(data);
    }
}
