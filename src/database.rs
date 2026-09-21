/**
 * Database app, used to write to/read from an sqlite database
 * - Ingest/Egress with json?
 */

/**
 * Required fields:
 * - timestamp
 * - vector of "key" : "values"
 *   - values are all strings? easy for now...
 */
use chrono::{DateTime, Utc};
use rusqlite::{Connection, ToSql, params, types::ToSqlOutput};
use serde::Serialize;
use serde_json::Value;

/// One value. Stored in the NUMERIC column; SQLite keeps ints as INTEGER
/// and f64 as REAL, so nothing is lost.
pub enum Data {
    Double(f64),
    Int64(i64),
    UInt64(u64),
}

/* Implementing ToSql for my Data type - see the rusqlite use call */
/* Im stacking vtable calls! */
impl ToSql for Data {
    fn to_sql(&self) -> rusqlite::Result<ToSqlOutput<'_>> {
        match self {
            Data::Double(v) => v.to_sql(),
            Data::Int64(v) => v.to_sql(),
            // SQLite has no u64; cast (or store as f64 / TEXT if you need > i64::MAX)
            // Data::UInt64(v) => (*v as i64).to_sql(), <- v is a temporary,
            Data::UInt64(v) => Ok(ToSqlOutput::from(*v as i64)),
        }
    }
}

/// One key/value pair
pub struct Measurement {
    pub key: String,
    pub data: Data,
}

/// What callers pass in: one timestamp + many key/value pairs.
/// Each pair becomes its own row in `measurements`, all sharing the timestamp.
pub struct Record {
    pub timestamp: DateTime<Utc>,
    pub measurements: Vec<Measurement>,
}

impl Record {
    /// Flatten any Serialize struct into key/value pairs.
    /// Nested structs become dotted keys ("pack.voltage"), arrays become "cells.0".
    /// serde attributes (skip, rename, flatten) apply as usual.

    // Type T must implement Serialize trait ->   #[derive(Serialize)]
    pub fn from_struct<T: Serialize>(timestamp: DateTime<Utc>, struct_data: &T) -> Self {
        let mut measurements: Vec<Measurement> = Vec::new();
        /* Start flattening with a null root */
        flatten(
            "",
            // Using json to serialize our struct to avoid extra code
            &serde_json::to_value(struct_data).expect("Record structs must be json serializable"),
            // feed the measurements buffer
            &mut measurements,
        );
        Record {
            timestamp,
            measurements,
        }
    }
}

/**
 * @brief Recursively search through the given object -> extract the string name of each field using json
 *
 * Value is actually an enum
 */
fn flatten(prefix: &str, v: &Value, out: &mut Vec<Measurement>) {
    // This is rust's lambda equivalent, called a "closure"
    let join = |k: &str| {
        if prefix.is_empty() {
            // If prefix is empty, nothing has been generated -> just return k
            return k.to_string();
        } else {
            // If prefix isnt empty, we have some root -> append k
            return format!("{prefix}.{k}");
        }
    };
    // Switch case through the value type in json
    // array and object recurse until they hit a leaf (bool or integer), once that is hit we return with the output vector updated
    match v {
        // Value is a nested struct or a hashmap
        Value::Object(map) => {
            for (k, v) in map {
                flatten(&join(k), v, out);
            }
        }
        // Value is an array
        Value::Array(arr) => {
            for (i, v) in arr.iter().enumerate() {
                flatten(&join(&i.to_string()), v, out);
            }
        }
        // Value is some number
        Value::Number(n) => {
            let data = if let Some(i) = n.as_i64() {
                Data::Int64(i)
            } else if let Some(u) = n.as_u64() {
                Data::UInt64(u)
            } else {
                Data::Double(n.as_f64().unwrap())
            };
            out.push(Measurement {
                key: prefix.to_string(),
                data,
            });
        }
        Value::Bool(b) => out.push(Measurement {
            key: prefix.to_string(),
            data: Data::Int64(*b as i64),
        }),
        // Null / String: skipped. Add a Data::Text variant if you want them stored.
        _ => {}
    }
}

pub struct Database {
    connection: Connection,
}

impl Database {
    pub fn new(path: &str) -> rusqlite::Result<Self> {
        let conn = Connection::open(path)?;
        // FIXME: May need to modify the default timeout here, tbd...

        conn.execute(
            "CREATE TABLE IF NOT EXISTS `measurements` (
              `id` integer not null primary key autoincrement,
              `timestamp` DATETIME null,
              `key` TEXT null,
              `value` NUMERIC null
            )",
            [],
        )?;

        // Add a trigger to cap the table at 50,000 rows
        conn.execute(
            "CREATE TRIGGER IF NOT EXISTS limit_measurements_trigger
             AFTER INSERT ON measurements
             BEGIN
                 DELETE FROM measurements WHERE id <= (SELECT MAX(id) FROM measurements) - 50000;
             END;",
            [],
        )?;

        Ok(Self { connection: conn })
    }

    pub fn insert(&mut self, record: &Record) -> rusqlite::Result<()> {
        // One transaction so N pairs = 1 fsync, and it's all-or-nothing
        let tx = self.connection.transaction()?;
        {
            let mut stmt: rusqlite::CachedStatement<'_> = tx.prepare_cached(
                "INSERT INTO measurements (timestamp, key, value) VALUES (?1, ?2, ?3)",
            )?;
            for m in &record.measurements {
                // This runs the execute call on the stmt value with timestamp/key/data values using the ToSql trait on data!
                // ToSql implementation add to_sql() vtable entries
                stmt.execute(params![record.timestamp, m.key, m.data])?;
            }
        }
        return tx.commit();
    }
}

// Compiled only under `cargo test`
#[cfg(test)]
mod tests {
    use super::*;

    #[derive(Serialize)]
    struct Pack {
        voltage: f64,
        cells: [f64; 2],
        ok: bool,
    }

    fn sample() -> Pack {
        Pack {
            voltage: 48.0,
            cells: [3.7, 3.8],
            ok: true,
        }
    }

    // --- flatten / from_struct: no DB needed ---

    // serde_json::Map is a BTreeMap, so keys come out sorted, not in field order
    #[test]
    fn flatten_produces_dotted_keys() {
        let r = Record::from_struct(Utc::now(), &sample());
        let keys: Vec<&str> = r.measurements.iter().map(|m| m.key.as_str()).collect();
        assert_eq!(keys, ["cells.0", "cells.1", "ok", "voltage"]);
    }

    #[test]
    fn bool_becomes_int() {
        let r = Record::from_struct(Utc::now(), &sample());
        let ok = r.measurements.iter().find(|m| m.key == "ok").unwrap();
        assert!(matches!(ok.data, Data::Int64(1)));
    }

    // --- Database: ":memory:" so no files are touched ---

    #[test]
    fn insert_writes_one_row_per_measurement() {
        let mut db = Database::new(":memory:").unwrap();
        db.insert(&Record::from_struct(Utc::now(), &sample())).unwrap();

        let n: i64 = db
            .connection
            .query_row("SELECT COUNT(*) FROM measurements", [], |r| r.get(0))
            .unwrap();
        assert_eq!(n, 4);
    }

    #[test]
    fn insert_stores_values() {
        let mut db = Database::new(":memory:").unwrap();
        db.insert(&Record::from_struct(Utc::now(), &sample())).unwrap();

        let v: f64 = db
            .connection
            .query_row(
                "SELECT value FROM measurements WHERE key = 'cells.1'",
                [],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(v, 3.8);
    }

    #[test]
    fn table_is_capped_at_50000_rows() {
        #[derive(Serialize)]
        struct One {
            v: i64,
        }

        let mut db = Database::new(":memory:").unwrap();
        // One row per insert; go one past the cap so the trigger fires
        for i in 0..50_001 {
            db.insert(&Record::from_struct(Utc::now(), &One { v: i }))
                .unwrap();
        }

        let (n, min_id, max_id): (i64, i64, i64) = db
            .connection
            .query_row(
                "SELECT COUNT(*), MIN(id), MAX(id) FROM measurements",
                [],
                |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
            )
            .unwrap();

        assert_eq!(n, 50_000);
        assert_eq!(max_id, 50_001);
        assert_eq!(min_id, 2); // oldest row (id 1) was dropped
    }
}

// ---------------------------------------------------------------------------
// Old version, kept for reference
// ---------------------------------------------------------------------------
//
// struct Database {
//     connection: Connection,
// }
// use chrono::{DateTime, Utc};
//
// impl Database {
//     pub fn new(path: &str) -> Option<Self> {
//         let conn = Connection::open(path);
//         // FIXME: May need to modify the default timeout here, tbd...
//
//         // If we fail to execute, return None
//         conn.execute(
//             "create table `measurements` (
//               `id` integer not null primary key autoincrement,
//               `timestamp` DATETIME null,
//               `key` TEXT null,
//               `value` NUMERIC null
//             )",
//             [],
//         )?;
//
//         // 2. Add a trigger to cap the table at 50,000 rows
//         conn.execute(
//             "CREATE TRIGGER IF NOT EXISTS limit_measurements_trigger
//              AFTER INSERT ON measurements
//              BEGIN
//                  DELETE FROM measurements WHERE id <= (SELECT MAX(id) FROM measurements) - 50000;
//              END;",
//             [],
//         )?;
//
//         Some(Self { connection: conn })
//     }
//
//     // FIXME: Need to think about how to handle this, hmmm...
//     enum Data
//     {
//         Double(f64),
//         Int64(i64),
//         UInt64(u64),
//     }
//
//     struct KeyData
//     {
//
//
//         data: Data
//     }
//
//     pub struct DbField
//     {
//         timestamp : DateTime<Utc>,
//         key_data : Vec<KeyData>
//     }
//
//     pub fn insert_data()
//     {
//
//     }
// }
