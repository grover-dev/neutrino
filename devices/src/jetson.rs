/* Record cpu telemetry, i.e.:
 * - RAM, CPU usage
 * - Temperature
 * - Compute mode (15 W is the goal)
 * - power consumption
 */
use sysinfo::Components;

enum ComputeMode {
    _7W,
    _15W,
    _25W,
}

struct JetsonData {
    cpu_util: f32,
    ram_util_megabytes: f32,
    temperatures: f32, // figure out how many the jetson is gonna shit out
    compute_mode: ComputeMode,
}

struct Jetson {
    // FIXME: Open files
    temperatures: sysinfo::Components,
}

impl Jetson {
    pub fn new() -> Option<Jetson> {
        let Ok(temperature_components) = Components::new_with_refreshed_list() else {
            return None;
        };

        Some(Self {
            temperatures: temperature_components,
        })
    }

    pub fn read_temps() -> JetsonData {}
}
