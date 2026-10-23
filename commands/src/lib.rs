use serde::{Deserialize, Serialize};
#[derive(Default, Debug, Copy, Clone, Serialize, Deserialize)]
pub struct CommandRequest {
    pub command_id: u16,
    pub struct_hash: u32,
    pub field_hash: u32,
    pub value: u64,
}

#[derive(Default, Debug, Copy, Clone, Serialize, Deserialize)]
pub enum Resp {
    #[default]
    Failure,
    Success,
}

#[derive(Default, Debug, Copy, Clone, Serialize, Deserialize)]
pub struct CommandResponse {
    pub command_id: u16,
    pub response: Resp,
}
