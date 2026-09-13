/*
 * fuck it we ball
 */
// #![no_std]
mod mppt;
use mppt::VictronData;

fn main() {
    // println!("Hello, world!");
    let mut mppt: mppt::VictronMppt =
        mppt::VictronMppt::new("/dev/ttyUSB0", mppt::BaudRate::_19200);

    loop {
        let data: Option<VictronData> = mppt.poll();

        if let Some(value) = data {
            println!("{:?}", value);
        }
    }
}
