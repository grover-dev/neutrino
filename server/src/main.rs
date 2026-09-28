use db::{Database, Measurement, Record};
use serde::Deserialize;
use serde_json::{Value, json};
/**
 * @brief Server structure
 * - for now single thread this, accept a json element
 *   - must include "type" : "telemetry"/"command"/"response" field?
 *     - Telemetry just gets written to the DB (tbd how slow this is gonna be...)
 *     - command gets parsed -> only support fetch range ops for now?
 *       - issue a response
 *   - hmmm... TBD how to structure this, maybe this will be reusable on both the remote and the local system... everything is json ([1,5] Hz is slow enough!)
 */
use std::net::UdpSocket;
use std::thread;
use std::time::Duration;

fn main() {
    let duration = Duration::from_millis(50);

    let mut db = Database::new("telemetry1.db").unwrap();

    // FIXME: oh boy how to do socket alloc :kermit-falling:
    let socket = UdpSocket::bind("127.0.0.1:9001").expect("Failed to open socket");
    let mut buffer: [u8; 1500] = [0; 1500];

    loop {
        /* Start with a sleep so that all continues rate limit the thread */
        thread::sleep(duration);

        let Ok((amt, _)) = socket.recv_from(&mut buffer) else {
            continue;
        };

        let Ok(json_str) = std::str::from_utf8(&buffer[..amt]) else {
            continue;
        };

        /* Type specification required to tell compiler which version of from_str im trying to use */
        let Ok(data): Result<Value, _> = serde_json::from_str(json_str) else {
            continue;
        };

        if data.get("type").is_none() {
            continue;
        }

        let Some(Value::String(type_str)) = data.get("type") else {
            continue;
        };

        match type_str.as_str() {
            "telemetry" => {
                let Some(record) = db::Record::from_json(&data) else {
                    continue;
                };
                // FIXME: Probably log?
                _ = db.insert(&record);
            }
            "command" => { /* FIXME: implement to fetch data... */ }
            "response" => { /* TODO: handle? or ignore? tbd... */ }
            _ => continue,
        }
    }
}
