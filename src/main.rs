/*
 * fuck it we ball
 */
// #![no_std]
mod mppt;

fn main() {
    // println!("Hello, world!");
    let mut mppt: mppt::VictronMppt =
        mppt::VictronMppt::new("/dev/ttyUSB0", mppt::BaudRate::_19200);

    mppt.poll();
}
