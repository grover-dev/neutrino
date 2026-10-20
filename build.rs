// Writes generated/schema/<StructName>.yaml for every schema_struct! in our dependencies
use schema::{SchemaEntry, inventory};
use std::{fs, path::PathBuf};

// Without a reference to `devices`, it isn't linked in and its structs never register
use devices as _;

fn main() {
    // Rerun when this file changes; cargo also reruns it whenever a build-dependency changes
    println!("cargo:rerun-if-changed=build.rs");

    let out_dir = PathBuf::from(std::env::var("CARGO_MANIFEST_DIR").unwrap()).join("generated/schema");
    fs::create_dir_all(&out_dir).unwrap();

    for s in inventory::iter::<SchemaEntry> {
        let mut yaml = format!("name: {}\nhash: {:#010x}\nfields:\n", s.name, s.hash);
        for f in s.fields {
            yaml.push_str(&format!("  - {{ name: {}, type: {}, hash: {:#010x} }}\n", f.name, f.ty, f.hash));
        }
        fs::write(out_dir.join(format!("{}.yaml", s.name)), yaml).unwrap();
    }
}
