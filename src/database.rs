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

struct Database {
    connection: Connection,
}

struct

impl Database {
    pub fn new(path: &str) -> Option<Self> {
        let conn = Connection::open(path);
        // FIXME: May need to modify the default timeout here, tbd...

        // If we fail to execute, return None
        conn.execute(
            "create table `measurements` (
              `id` integer not null primary key autoincrement,
              `timestamp` DATETIME null,
              `key` TEXT null,
              `value` NUMERIC null
            )",
            [],
        )?;

        // 2. Add a trigger to cap the table at 50,000 rows
        conn.execute(
            "CREATE TRIGGER IF NOT EXISTS limit_measurements_trigger
             AFTER INSERT ON measurements
             BEGIN
                 DELETE FROM measurements WHERE id <= (SELECT MAX(id) FROM measurements) - 50000;
             END;",
            [],
        )?;

        Some(Self { connection: conn })
    }

    // FIXME: Need to think about how to handle this, hmmm...
    pub fn insert_data()
    {

    }
}
