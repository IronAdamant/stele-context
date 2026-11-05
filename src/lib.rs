//! stele-context 2.0. Standard library only.

pub mod chunk;
pub mod cli;
pub mod config;
pub mod contract;
pub mod doctor;
pub mod engine;
pub mod gitignore;
pub mod hash;
pub mod http_serve;
pub mod json;
pub mod lang;
pub mod locks;
pub mod mcp;
pub mod media;
#[cfg(test)]
pub(crate) mod oracle;
pub mod pyre;
pub mod pyver;
pub mod root;
pub mod search;
pub mod store;
pub mod symbols;
pub mod syntax;
pub mod walk;

pub const VERSION: &str = env!("CARGO_PKG_VERSION");
pub const SCHEMA_VERSION: &str = "2.1.0";

#[cfg(test)]
mod zero_deps {
    #[test]
    fn cargo_manifest_and_lock_are_std_only() {
        let manifest = include_str!("../Cargo.toml");
        assert!(
            dependency_tables_empty(manifest),
            "Cargo.toml dependency tables must stay empty"
        );
        let names = lock_package_names(include_str!("../Cargo.lock"));
        assert_eq!(names, vec!["stele-context".to_string()]);
    }

    fn dependency_tables_empty(text: &str) -> bool {
        let mut section = String::new();
        for raw in text.lines() {
            let line = raw.split('#').next().unwrap_or("").trim();
            if line.is_empty() {
                continue;
            }
            if line.starts_with('[') && line.ends_with(']') {
                section = line[1..line.len() - 1].trim().to_string();
                continue;
            }
            let dep = section == "dependencies"
                || section == "dev-dependencies"
                || section == "build-dependencies"
                || section.starts_with("dependencies.")
                || section.starts_with("dev-dependencies.")
                || section.starts_with("build-dependencies.");
            if dep {
                return false;
            }
            let compact: String = line.chars().filter(|ch| !ch.is_whitespace()).collect();
            if section == "package" && compact.starts_with("build=") {
                return false;
            }
        }
        true
    }

    fn lock_package_names(text: &str) -> Vec<String> {
        let mut names = Vec::new();
        let mut in_package = false;
        for raw in text.lines() {
            let line = raw.trim();
            if line == "[[package]]" {
                in_package = true;
                continue;
            }
            if in_package {
                if let Some(rest) = line.strip_prefix("name = ") {
                    names.push(rest.trim_matches('"').to_string());
                    in_package = false;
                }
            }
        }
        names
    }
}
