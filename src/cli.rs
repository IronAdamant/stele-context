//! Hand-written argument parser. JSON is the default for commands.
//! `--version` and `--help` stay single-purpose text, matching the bench.

use crate::contract::{ErrorBody, Response, EXIT_USAGE};
use crate::doctor;
use crate::VERSION;
use std::env;
use std::ffi::OsString;
use std::path::PathBuf;

pub fn run() -> i32 {
    let args: Vec<String> = env::args_os().skip(1).map(lossy).collect();
    dispatch(&args).emit()
}

pub fn dispatch(args: &[String]) -> Response {
    if invoked_as_mcp() {
        return Response::failure(
            "serve-mcp",
            crate::contract::EXIT_ERROR,
            ErrorBody::new(
                "not_in_this_build",
                "the stdio MCP server is not in this build",
                "MCP arrives in a later 2.0 phase; this binary does not pretend to serve",
            ),
        );
    }
    match parse(args) {
        Ok(parsed) => parsed.run(),
        Err(message) => Response::failure(
            "",
            EXIT_USAGE,
            ErrorBody::new("usage", message, "run stele-context --help"),
        ),
    }
}

struct Parsed {
    help: bool,
    version: bool,
    human: bool,
    storage: Option<PathBuf>,
    command: Option<String>,
}

impl Parsed {
    fn run(self) -> Response {
        if self.version {
            return Response::text(0, &format!("stele-context {VERSION}"));
        }
        match self.command.as_deref() {
            None | Some("help") => Response::text(0, &help_text()),
            Some("doctor") => {
                if self.help {
                    return Response::text(0, &help_text());
                }
                doctor::report(self.storage.as_deref(), self.human)
            }
            Some(other) => Response::failure(
                other,
                EXIT_USAGE,
                ErrorBody::new(
                    "unknown_command",
                    format!("unknown command '{other}'"),
                    "this build implements doctor; indexing and search arrive in later phases",
                ),
            ),
        }
    }
}

fn parse(args: &[String]) -> Result<Parsed, String> {
    let mut parsed = Parsed {
        help: false,
        version: false,
        human: false,
        storage: None,
        command: None,
    };
    let mut index = 0;
    while index < args.len() {
        let arg = &args[index];
        if arg == "--" {
            index += 1;
            break;
        }
        if let Some(path) = arg.strip_prefix("--storage-dir=") {
            parsed.storage = Some(PathBuf::from(path));
            index += 1;
            continue;
        }
        match arg.as_str() {
            "--version" => parsed.version = true,
            "--help" | "-h" => parsed.help = true,
            "--human" => parsed.human = true,
            "--json" => {}
            "--storage-dir" => {
                index += 1;
                let path = args
                    .get(index)
                    .ok_or_else(|| "--storage-dir needs a path".to_string())?;
                parsed.storage = Some(PathBuf::from(path));
            }
            other if other.starts_with('-') => {
                return Err(format!("unknown flag '{other}'"));
            }
            other => {
                if parsed.command.is_some() {
                    return Err(format!("unexpected argument '{other}'"));
                }
                parsed.command = Some(other.to_string());
            }
        }
        index += 1;
    }
    if index < args.len() {
        return Err(format!("unexpected argument '{}'", args[index]));
    }
    if parsed.help && parsed.command.is_none() {
        parsed.command = None;
    }
    Ok(parsed)
}

fn help_text() -> String {
    format!(
        "\
stele-context {VERSION}

Local context cache for AI coding agents.

Usage:
  stele-context [--storage-dir DIR] [--human] <command>

Commands:
  doctor    Report version, project root, storage, and whether a Python store is present

JSON is the default on commands. --human prints text.
--version prints the version line and exits.
"
    )
}

fn invoked_as_mcp() -> bool {
    env::current_exe()
        .ok()
        .and_then(|path| {
            path.file_stem()
                .map(|stem| stem.to_string_lossy().to_string())
        })
        .is_some_and(|name| name == "stele-context-mcp")
}

fn lossy(arg: OsString) -> String {
    arg.to_string_lossy().into_owned()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::json;

    #[test]
    fn version_line_is_twenty_bytes() {
        let response = dispatch(&["--version".to_string()]);
        assert_eq!(response.code, 0);
        assert_eq!(response.stdout, b"stele-context 2.0.0\n");
        assert_eq!(response.stdout.len(), 20);
    }

    #[test]
    fn doctor_json_is_honest() {
        let response = dispatch(&["doctor".to_string()]);
        assert_eq!(response.code, 0);
        let text = std::str::from_utf8(&response.stdout).unwrap();
        let value = json::parse(text.trim_end()).unwrap();
        let rendered = json::to_string(&value);
        assert!(rendered.contains("\"ok\":true"));
        assert!(rendered.contains("\"version\":\"2.0.0\""));
        assert!(rendered.contains("\"implementation\":\"rust\""));
        assert!(rendered.contains("No 2.0 store yet") || rendered.contains("native store"));
    }

    #[test]
    fn unknown_command_is_not_ok() {
        let response = dispatch(&["index".to_string()]);
        assert_eq!(response.code, 2);
        let text = std::str::from_utf8(&response.stdout).unwrap();
        assert!(text.contains("\"ok\":false"));
        assert!(text.contains("unknown_command"));
    }

    #[test]
    fn help_mentions_doctor() {
        let response = dispatch(&["--help".to_string()]);
        assert_eq!(response.code, 0);
        let text = std::str::from_utf8(&response.stdout).unwrap();
        assert!(text.contains("doctor"));
    }
}
