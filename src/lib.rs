//! stele-context 2.0. Standard library only.

pub mod chunk;
pub mod cli;
pub mod config;
pub mod contract;
pub mod doctor;
pub mod engine;
pub mod gitignore;
pub mod hash;
pub mod json;
pub mod lang;
pub mod mcp;
pub mod pyre;
pub mod root;
pub mod search;
pub mod store;
pub mod symbols;
pub mod syntax;
pub mod walk;

pub const VERSION: &str = env!("CARGO_PKG_VERSION");
pub const SCHEMA_VERSION: &str = "2.0.0";
