/*
 * fuck it we ball
 */
// #![no_std]
mod mppt;
use mppt::VictronData;

mod bms;
use bms::DynessBmsData;

fn main() {
    // println!("Hello, world!");
    // let mut mppt: mppt::VictronMppt =
    //     mppt::VictronMppt::new("/dev/ttyUSB0", mppt::BaudRate::_19200);

    // loop {
    //     let data: Option<VictronData> = mppt.poll();

    //     if let Some(value) = data {
    //         println!("{:?}", value);
    //     }
    // }

    let mut bms: bms::DynessBms = bms::DynessBms::new("127.0.0.1:9000");

    loop {
        let data: Option<DynessBmsData> = bms.poll();

        if let Some(value) = data {
            println!("{:?}", value);
        }
    }
}
