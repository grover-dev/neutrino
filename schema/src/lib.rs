/// and on the seventh day, claude said let their be a free code generator

// FNV-1a: a tiny, well-known hash. Easy to reproduce in Python or C.
pub const fn hash_str(s: &str) -> u32 {
    let bytes = s.as_bytes();
    let mut h: u32 = 0x811c9dc5;
    let mut i = 0;
    while i < bytes.len() {
        h ^= bytes[i] as u32;
        h = h.wrapping_mul(0x01000193);
        i += 1;
    }
    h
}

pub struct FieldInfo {
    pub name: &'static str,
    pub ty: &'static str,
    pub hash: u32,
}

// Re-exported so schema_struct! callers don't need their own inventory dependency
pub use inventory;

// One entry per schema_struct!, registered automatically
pub struct SchemaEntry {
    pub name: &'static str,
    pub hash: u32,
    pub fields: &'static [FieldInfo],
}

inventory::collect!(SchemaEntry);

#[derive(Debug, PartialEq)]
pub enum SetError {
    StructMismatch,
    UnknownField,
    OutOfRange,
}

pub trait Schema {
    const NAME: &'static str;
    const HASH: u32;
    const FIELDS: &'static [FieldInfo];

    fn set_field(&mut self, field_hash: u32, raw: u64) -> Result<(), SetError>;

    fn apply(&mut self, struct_hash: u32, field_hash: u32, raw: u64) -> Result<(), SetError> {
        if struct_hash != Self::HASH {
            return Err(SetError::StructMismatch);
        }
        self.set_field(field_hash, raw)
    }
}

pub trait FromRaw64: Sized {
    fn from_raw(raw: u64) -> Option<Self>;
}

impl FromRaw64 for f32 {
    fn from_raw(raw: u64) -> Option<Self> {
        Some(f32::from_bits(raw as u32))
    }
}
impl FromRaw64 for f64 {
    fn from_raw(raw: u64) -> Option<Self> {
        Some(f64::from_bits(raw))
    }
}
impl FromRaw64 for bool {
    fn from_raw(raw: u64) -> Option<Self> {
        Some(raw != 0)
    }
}

macro_rules! unsigned { ($($t:ty),*) => { $(impl FromRaw64 for $t {
    fn from_raw(raw: u64) -> Option<Self> { <$t>::try_from(raw).ok() }
})* } }
macro_rules! signed { ($($t:ty),*) => { $(impl FromRaw64 for $t {
    fn from_raw(raw: u64) -> Option<Self> { <$t>::try_from(raw as i64).ok() }
})* } }
unsigned!(u8, u16, u32, u64);
signed!(i8, i16, i32, i64);

/// Defines a struct and implements `Schema` for it.
/// Field hash = hash("name:type"), struct hash = hash("Name{a:t,b:t,}").
#[macro_export]
macro_rules! schema_struct {
    (
        $(#[$meta:meta])*
        $vis:vis struct $name:ident {
            $( $(#[$fmeta:meta])* $fvis:vis $field:ident : $ty:ty ),* $(,)?
        }
    ) => {
        $(#[$meta])*
        $vis struct $name {
            $( $(#[$fmeta])* $fvis $field: $ty ),*
        }

        $crate::inventory::submit! {
            $crate::SchemaEntry {
                name: <$name as $crate::Schema>::NAME,
                hash: <$name as $crate::Schema>::HASH,
                fields: <$name as $crate::Schema>::FIELDS,
            }
        }

        impl $crate::Schema for $name {
            const NAME: &'static str = stringify!($name);
            const HASH: u32 = $crate::hash_str(concat!(
                stringify!($name), "{", $( stringify!($field), ":", stringify!($ty), ",", )* "}"
            ));
            const FIELDS: &'static [$crate::FieldInfo] = &[$(
                $crate::FieldInfo {
                    name: stringify!($field),
                    ty: stringify!($ty),
                    hash: $crate::hash_str(concat!(stringify!($field), ":", stringify!($ty))),
                }
            ),*];

            fn set_field(&mut self, field_hash: u32, raw: u64) -> Result<(), $crate::SetError> {
                $(
                    if field_hash == const { $crate::hash_str(concat!(stringify!($field), ":", stringify!($ty))) } {
                        self.$field = <$ty as $crate::FromRaw64>::from_raw(raw)
                            .ok_or($crate::SetError::OutOfRange)?;
                        return Ok(());
                    }
                )*
                Err($crate::SetError::UnknownField)
            }
        }
    };
}

use std::{
    collections::HashSet,
    env, fs, io,
    path::{Path, PathBuf},
};

pub fn write_yaml_dir(dir: &Path) -> io::Result<()> {
    let out_dir = PathBuf::from(dir).join("generated/schema");
    fs::create_dir_all(&out_dir).unwrap();

    let mut written = HashSet::new();
    for s in inventory::iter::<SchemaEntry> {
        let mut yaml = format!("name: {}\nhash: {:#010x}\nfields:\n", s.name, s.hash);
        for f in s.fields {
            yaml.push_str(&format!(
                "  - {{ name: {}, type: {}, hash: {:#010x} }}\n",
                f.name, f.ty, f.hash
            ));
        }
        let path = out_dir.join(format!("{}.yaml", s.name));
        fs::write(&path, yaml).unwrap();
        written.insert(path);
    }

    // Nice to have but causes conflicts with cross crate builds...
    // Remove YAML left behind by structs that were renamed or deleted
    // for entry in fs::read_dir(&out_dir).unwrap() {
    //     let path = entry.unwrap().path();
    //     if path.extension().is_some_and(|e| e == "yaml") && !written.contains(&path) {
    //         fs::remove_file(&path).unwrap();
    //     }
    // }
    return Ok(());
}

pub fn generate() {
    // Rerun when this file changes; cargo also reruns it whenever a build-dependency changes
    println!("cargo:rerun-if-changed=build.rs");

    let root = PathBuf::from(
        env::var("WORKSPACE_DIR").expect("WORKSPACE_DIR not set, see .cargo/config.yaml"),
    );
    write_yaml_dir(&root).expect("failed to write schema yaml");
}

#[macro_export]
macro_rules! build_script
{
    ($($krate:ident),* $(,)?)=>
    {
        // Inject the crate
        $( use $krate as _ ;)*
        fn main() {$crate::generate();}
    };
}

#[cfg(test)]
mod tests {
    use super::*;

    schema_struct! {
        #[derive(Default)]
        struct TestCommand {
            duty_cycle: f32,
            enable: u32,
            offset: i16,
        }
    }

    #[test]
    fn apply_sets_each_field() {
        let mut cmd = TestCommand::default();
        let h = TestCommand::HASH;

        cmd.apply(h, hash_str("duty_cycle:f32"), 0.75f32.to_bits() as u64)
            .unwrap();
        cmd.apply(h, hash_str("enable:u32"), 1).unwrap();
        cmd.apply(h, hash_str("offset:i16"), -5i64 as u64).unwrap();

        assert_eq!(cmd.duty_cycle, 0.75);
        assert_eq!(cmd.enable, 1);
        assert_eq!(cmd.offset, -5);
    }

    #[test]
    fn apply_rejects_mismatches() {
        let mut cmd = TestCommand::default();
        let h = TestCommand::HASH;

        assert_eq!(
            cmd.apply(h ^ 1, hash_str("enable:u32"), 1),
            Err(SetError::StructMismatch)
        );
        assert_eq!(
            cmd.apply(h, hash_str("enable:u8"), 1),
            Err(SetError::UnknownField)
        );
        assert_eq!(
            cmd.apply(h, hash_str("enable:u32"), u64::MAX),
            Err(SetError::OutOfRange)
        );
    }

    #[test]
    fn registers_with_inventory() {
        assert!(
            inventory::iter::<SchemaEntry>
                .into_iter()
                .any(|s| s.name == "TestCommand")
        );
    }
}
