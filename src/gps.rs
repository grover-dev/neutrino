/**
 * Ingest nmea messages -> convert to telemetry
 */
// FIXME: Tbd if this is good enough
use nmea0183::{ParseResult, Parser};
use serialport::{ClearBuffer, SerialPort};
use std::time::Duration;

pub struct Gps {
    port: Box<dyn SerialPort>,
    parser: Parser,
}

// FIXME: add checksunm checking?
#[derive(Default, Debug, Copy, Clone)]
pub struct GpsData {
    latitude: f64,
    longitude: f64,
    speed_knots: f32,
    course_deg_true: Option<f32>,
    active: bool,
    // FIXME: TODO, add compass correction when/if we get one
    //       - magnetic variation will be required to correct magnetic north to true north
    //       - gps tells us the course
    magnetic_correction: Option<f32>,
}

impl Gps {
    pub fn new(portname: &str) -> Self {
        let port = serialport::new(portname, 9_600)
            .timeout(Duration::from_millis(10))
            .open()
            // FIXME: tbd... eventually change this to do best effort sao that we dont crash if the mppt fails to open
            .expect("Failed to open port");

        // Flush after open, dont care about return
        let _ = port.clear(ClearBuffer::Input);
        let mut parser = Parser::new();
        Self {
            port: port,
            parser: parser,
        }
    }

    pub fn update(&mut self) -> Result<GpsData, GpsError> {
        let mut buffer: [u8; 512] = [0; 512];
        let Ok(bytes_read) = self.port.read(&mut buffer) else {
            return Err(GpsError::NoData);
        };

        parse_bytes(&mut self.parser, &buffer[..bytes_read])
    }
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum GpsError {
    /// No RMC sentence came in (timeout, read error, other sentences, or garbage)
    NoData,
    /// Got an RMC sentence, but the receiver doesn't have a position fix yet
    NoFix,
}

fn parse_bytes(parser: &mut Parser, buffer: &[u8]) -> Result<GpsData, GpsError> {
    for result in parser.parse_from_bytes(buffer) {
        match result {
            Ok(ParseResult::RMC(Some(rmc))) => {
                return Ok(GpsData {
                    latitude: rmc.latitude.as_f64(),
                    longitude: rmc.longitude.as_f64(),
                    speed_knots: rmc.speed.as_knots(), // FIXME: may replace this? tbd..
                    course_deg_true: rmc.course.clone().map(|s| s.degrees), // rust what the hell is this
                    active: true,                                           // FIXME: delete
                    magnetic_correction: None, // FIXME: nmea0183 is cucking me rmc.magnetic.clone().map(|s| s.degrees),
                });
            }
            // The crate gives back RMC(None) when there's no fix
            Ok(ParseResult::RMC(None)) => return Err(GpsError::NoFix),
            // Don't care about the other sentences
            Ok(_) | Err(_) => {}
        }
    }

    Err(GpsError::NoData)
}

#[cfg(test)]
mod tests {
    use super::*;

    // Captured from the receiver before it had a fix: RMC status V, no lat/lon
    const NO_FIX: &[u8] = b"$GNRMC,053513.00,V,,,,,,,021026,,,N*65\r\n\
$GNVTG,,,,,,,,,N*2E\r\n\
$GNGGA,053513.00,,,,,0,00,99.99,,,,,,*79\r\n\
$GNGSA,A,1,,,,,,,,,,,,,99.99,99.99,99.99*2E\r\n\
$GNGSA,A,1,,,,,,,,,,,,,99.99,99.99,99.99*2E\r\n\
$GPGSV,1,1,02,06,,,32,12,,,27*7A\r\n\
$GLGSV,1,1,01,,,,29*6F\r\n\
$GNGLL,,,,,053513.00,V,N*55\r\n";

    #[test]
    fn no_fix() {
        let mut parser = Parser::new();
        assert_eq!(
            parse_bytes(&mut parser, NO_FIX).unwrap_err(),
            GpsError::NoFix
        );
    }

    // Captured with a 3D fix, 12 satellites used
    const FIX: &[u8] = b"$GNRMC,064632.00,A,3352.33760,N,11822.65778,W,0.062,,021026,,,A*7F\r\n\
$GNVTG,,T,,M,0.062,N,0.116,K,A*3F\r\n\
$GNGGA,064632.00,3352.33760,N,11822.65778,W,1,12,1.19,40.8,M,-33.1,M,,*4A\r\n\
$GNGSA,A,3,12,21,11,13,25,29,24,,,,,,1.72,1.19,1.24*1E\r\n\
$GNGSA,A,3,81,87,88,78,79,,,,,,,,1.72,1.19,1.24*11\r\n\
$GPGSV,3,1,11,05,21,164,,11,70,047,23,12,74,333,32,13,80,322,25*77\r\n\
$GPGSV,3,2,11,19,19,070,20,21,33,127,09,22,00,130,,24,24,209,28*70\r\n\
$GPGSV,3,3,11,25,43,316,16,28,04,325,,29,17,278,19*48\r\n\
$GLGSV,3,1,09,71,00,032,,72,01,069,,76,00,152,,77,46,156,18*6C\r\n\
$GLGSV,3,2,09,78,70,318,22,79,17,329,28,81,27,243,22,87,27,033,12*65\r\n\
$GLGSV,3,3,09,88,67,314,32*5A\r\n\
$GNGLL,3352.33760,N,11822.65778,W,064632.00,A,A*65\r\n";

    #[test]
    fn fix() {
        let mut parser = Parser::new();
        let data = parse_bytes(&mut parser, FIX).unwrap();

        // 33° 52.33760' N, 118° 22.65778' W
        assert!((data.latitude - 33.872293).abs() < 1e-5);
        assert!((data.longitude - -118.377630).abs() < 1e-5);
        assert!((data.speed_knots - 0.062).abs() < 1e-3);
        assert_eq!(data.course_deg_true, None);
    }

    #[test]
    fn empty_input_is_no_data() {
        let mut parser = Parser::new();
        assert_eq!(parse_bytes(&mut parser, b"").unwrap_err(), GpsError::NoData);
    }
}
