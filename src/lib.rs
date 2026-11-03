//! stele-context 2.0. Standard library only.

pub mod cli;
pub mod config;
pub mod contract;
pub mod doctor;
pub mod gitignore;
pub mod hash;
pub mod json;
pub mod root;
pub mod walk;

pub const VERSION: &str = env!("CARGO_PKG_VERSION");
pub const SCHEMA_VERSION: &str = "2.0.0";
