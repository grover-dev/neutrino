use serde::Serialize;

schema::schema_struct! {
    #[derive(Default, Debug, Clone, Copy, Serialize)]
    pub struct VehicleWatchdog {
        pub keep_alive: u32,
    }
}

#[derive(Default, Debug, Clone, Copy, Serialize)]
pub struct VehicleWatchdogTelem {
    pub seconds_last_pet_time: u64,
    pub seconds_to_expiry: u64,
    pub watchdog_lockout_active: u32,
}
