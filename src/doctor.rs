//! Environment report. This build does not index; it says so instead of
//! inventing index counts.

use crate::config::Config;
use crate::contract::{ErrorBody, Response, EXIT_ERROR};
use crate::json::Value;
use crate::root::{detect_project_root, resolve_storage_dir};
use crate::VERSION;
use std::path::Path;

pub fn report(storage_flag: Option<&Path>, human: bool) -> Response {
    let cwd = match std::env::current_dir() {
        Ok(cwd) => cwd,
        Err(err) => {
            return Response::failure(
                "doctor",
                EXIT_ERROR,
                ErrorBody::new(
                    "cwd",
                    format!("current directory: {err}"),
                    "run from a real directory",
                ),
            );
        }
    };
    let project_root = detect_project_root(&cwd);
    let config = Config::load_at(project_root.as_deref());
    let storage = match resolve_storage_dir(storage_flag, &config, project_root.as_deref()) {
        Ok(path) => path,
        Err(message) => {
            return Response::failure(
                "doctor",
                EXIT_ERROR,
                ErrorBody::new("storage_dir", message, "pass --storage-dir or set HOME"),
            );
        }
    };
    let legacy = storage.join("stele_context.db").is_file();
    let native = storage.join("store").exists();
    let mut steps = Vec::new();
    if legacy {
        steps.push(
            "A Python 1.5.x stele_context.db is in the storage directory. It was left untouched. migrate is not in this build.",
        );
    }
    if native {
        steps.push("A native store file is present.");
    } else {
        steps.push("No 2.0 store yet. Indexing is not in this build.");
    }
    if human {
        let root = project_root
            .as_ref()
            .map(|path| path.display().to_string())
            .unwrap_or_else(|| "(none)".to_string());
        let text = format!(
            "stele-context {VERSION} (rust)\nproject root: {root}\nstorage: {}\nnative store: {}\nlegacy sqlite: {}\n{}",
            storage.display(),
            if native { "yes" } else { "no" },
            if legacy { "yes" } else { "no" },
            steps.join("\n"),
        );
        return Response::text(0, &text);
    }
    let result = Value::object(vec![
        ("version", Value::string(VERSION)),
        ("implementation", Value::string("rust")),
        (
            "project_root",
            match &project_root {
                Some(path) => Value::string(path.display().to_string()),
                None => Value::Null,
            },
        ),
        ("storage_dir", Value::string(storage.display().to_string())),
        ("config_loaded", Value::Bool(config.loaded)),
        ("native_store_present", Value::Bool(native)),
        ("legacy_sqlite_present", Value::Bool(legacy)),
        (
            "next_steps",
            Value::Array(steps.into_iter().map(Value::string).collect()),
        ),
    ]);
    Response::success("doctor", result)
}

impl Config {
    pub fn load_at(root: Option<&Path>) -> Config {
        crate::config::load(root)
    }
}
