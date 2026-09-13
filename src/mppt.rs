/*
 * Driver for the victron 100/30 MPPT
 * See https://www.victronenergy.com/upload/documents/VE.Direct-Protocol-3.34.pdf for reference
 * Example output:
 *
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
use std::io::{BufRead, BufReader};
use std::time::Duration;

#[derive(Default, Debug, Clone)]
#[repr(u8)]
pub enum OffReason {
    #[default]
    Unknown = 0,
    NoInputPower,
    SwitchedOffPowerSwitch,
    SwitchedOffDeviceModeRegister,
    RemoteInput,
    ProtectionActive,
    PayGo,
    Bms,
    EngineShutDownDetection,
    AnalysingInputVoltage,
}

impl From<u32> for OffReason {
    fn from(value: u32) -> Self {
        match value {
            0x00000001 => OffReason::NoInputPower,
            0x00000002 => OffReason::SwitchedOffPowerSwitch,
            0x00000004 => OffReason::SwitchedOffDeviceModeRegister,
            0x00000008 => OffReason::RemoteInput,
            0x00000010 => OffReason::ProtectionActive,
            0x00000020 => OffReason::PayGo,
            0x00000040 => OffReason::Bms,
            0x00000080 => OffReason::EngineShutDownDetection,
            0x00000100 => OffReason::AnalysingInputVoltage,
            _ => OffReason::Unknown,
        }
    }
}

#[derive(Default, Debug, Clone)]
pub enum Error {
    #[default]
    Unknown,
    NoError,
    BatteryVoltageTooHigh,
    ChargerTemperatureTooHigh,
    ChargerOverCurrent,
    ChargerCurrentReversed,
    BulkTimeLimitExceeded,
    CurrentSensorIssue,
    TerminalsOverheated,
    ConverterIssue,
    InputVoltageTooHigh,
    InputCurrentTooHigh,
    InputShutdownExcessiveBatteryVoltage,
    InputShutdownCurrentFlowDuringOffMode,
    LostCommunicationWithDevice,
    SynchronisedChargingDeviceConfigurationIssue,
    BmsConnectionLost,
    NetworkMisconfigured,
    FactoryCalibrationDataLost,
    InvalidIncompatibleFirmware,
    UserSettingsInvalid,
}

impl From<u32> for Error {
    fn from(value: u32) -> Self {
        match value {
            0 => Error::NoError,
            2 => Error::BatteryVoltageTooHigh,
            17 => Error::ChargerTemperatureTooHigh,
            18 => Error::ChargerOverCurrent,
            19 => Error::ChargerCurrentReversed,
            20 => Error::BulkTimeLimitExceeded,
            21 => Error::CurrentSensorIssue,
            26 => Error::TerminalsOverheated,
            28 => Error::ConverterIssue,
            33 => Error::InputVoltageTooHigh,
            34 => Error::InputCurrentTooHigh,
            38 => Error::InputShutdownExcessiveBatteryVoltage,
            39 => Error::InputShutdownCurrentFlowDuringOffMode,
            65 => Error::LostCommunicationWithDevice,
            66 => Error::SynchronisedChargingDeviceConfigurationIssue,
            67 => Error::BmsConnectionLost,
            68 => Error::NetworkMisconfigured,
            116 => Error::FactoryCalibrationDataLost,
            117 => Error::InvalidIncompatibleFirmware,
            119 => Error::UserSettingsInvalid,
            _ => Error::Unknown,
        }
    }
}

#[derive(Default, Debug, Clone)]
pub struct VictronData {
    voltage_v: f32,
    current_a: f32,
    solar_array_voltage_v: f32,
    solar_array_power_w: f32,
    load_on: bool,

    yield_total_kwh: f32,
    yield_today_kwh: f32,
    yield_yesterday_kwh: f32,
    maximum_power_today_w: f32,
    maximum_power_yesterday_w: f32,
    day_sequence: u16,
    off_reason: OffReason,
    off_reason_raw: u32,
    error: Error,
    error_raw: u32,
}

pub struct VictronMppt {
    port_reader: BufReader<Box<dyn SerialPort>>,
    data: VictronData,
    pid_found: bool,
}

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

        // Flush after open, dont care about return
        let _ = port.clear(ClearBuffer::Input);

        /* Wrap the port in a buffered reader */
        let reader = BufReader::new(port);

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
        let mut line = String::new(); // <- make this a member to prevent full allocation/deallocation every cyle... maybe pre buffer

        if let Ok(bytes_read) = self.port_reader.read_line(&mut line) {
            if bytes_read <= 0 {
                return None;
            }

            if line.trim_matches('\r').contains("PID") {
                if !self.pid_found {
                    /* Start a new block */
                    self.pid_found = true;
                } else {
                    /* Return what we have in the buffer, even if its not filled out */
                    return Some(self.data.clone());
                }
            } else if self.pid_found {
                let key: Option<&str> = line.split_whitespace().nth(0);
                let value: Option<&str> = line.split_whitespace().nth(1);

                if key == None || value == None {
                    return None;
                }

                let integer: i64 = value.unwrap().parse().ok().unwrap_or(0);
                let clean_hex = value.unwrap().strip_prefix("0x").unwrap_or(value.unwrap());
                let hex = u32::from_str_radix(clean_hex, 16).unwrap_or(0);

                /* See: https://www.victronenergy.com/upload/documents/VE.Direct-Protocol-3.34.pdf  */
                if key == Some("V") {
                    self.data.voltage_v = (integer as f32) / 1000.0;
                } else if key == Some("A") {
                    // FIXME: double check the units here
                    self.data.current_a = integer as f32;
                } else if key == Some("LOAD") {
                    self.data.load_on = if value == Some("ON") { true } else { false };
                } else if key == Some("VPV") {
                    self.data.solar_array_voltage_v = (integer as f32) / 1000.0;
                } else if key == Some("PPV") {
                    self.data.solar_array_power_w = (integer as f32) / 1000.0;
                } else if key == Some("H19") {
                    self.data.yield_total_kwh = (integer as f32) / 100.0;
                } else if key == Some("H20") {
                    self.data.yield_today_kwh = (integer as f32) / 100.0;
                } else if key == Some("H21") {
                    self.data.maximum_power_today_w = integer as f32;
                } else if key == Some("H22") {
                    self.data.yield_yesterday_kwh = (integer as f32) / 100.0;
                } else if key == Some("H23") {
                    self.data.maximum_power_yesterday_w = integer as f32;
                } else if key == Some("HDS") {
                    self.data.day_sequence = integer as u16;
                } else if key == Some("OR") {
                    self.data.off_reason_raw = hex;
                    self.data.off_reason = OffReason::from(hex);
                } else if key == Some("ERR") {
                    self.data.error_raw = integer as u32;
                    self.data.error = Error::from(integer as u32);
                }
            } else {
                /* No PID found, dont do anything yet */
                return None;
            }
        }

        return None;
    }
}
