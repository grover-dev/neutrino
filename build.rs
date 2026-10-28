// Writes generated/schema/<StructName>.yaml for every schema_struct! in our dependencies
use schema::{SchemaEntry, inventory};
use std::{collections::HashSet, fs, path::PathBuf};

// Without a reference to `devices`, it isn't linked in and its structs never register
// use devices as _;
//

schema::build_script! {devices}
