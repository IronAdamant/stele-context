//! Hand-written argument parser. JSON is the default for commands.
//! `--version` and `--help` stay single-purpose text, matching the bench.

use crate::contract::{ErrorBody, Response, EXIT_ERROR, EXIT_USAGE};
use crate::doctor;
use crate::engine::Engine;
use crate::json::Value;
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
        return crate::mcp::serve();
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
    positionals: Vec<String>,
    search_mode: String,
    symbol: Option<String>,
    session: Option<String>,
    action: Option<String>,
    top_k: usize,
    force: bool,
    scan_new: bool,
}

impl Parsed {
    fn run(self) -> Response {
        if self.version {
            return Response::text(0, &format!("stele-context {VERSION}"));
        }
        if self.help && self.command.is_none() {
            return Response::text(0, &help_text());
        }
        let Some(command) = self.command.clone() else {
            return Response::text(0, &help_text());
        };
        if self.help {
            return Response::text(0, &help_text());
        }
        if command == "doctor" {
            return doctor::report(self.storage.as_deref(), self.human);
        }
        if command == "serve-mcp" {
            return crate::mcp::serve();
        }
        let mut engine = match Engine::open(self.storage.as_deref()) {
            Ok(engine) => engine,
            Err(message) => {
                return Response::failure(
                    &command,
                    EXIT_ERROR,
                    ErrorBody::new("storage", message, "pass --storage-dir or set HOME"),
                );
            }
        };
        let result = self.execute(&mut engine);
        match result {
            Ok(value) => {
                if self.human {
                    Response::text(0, &crate::json::to_string(&value))
                } else {
                    Response::success(&command, value)
                }
            }
            Err(message) => Response::failure(
                &command,
                EXIT_ERROR,
                ErrorBody::new("command_failed", message, "see the message"),
            ),
        }
    }

    fn execute(&self, engine: &mut Engine) -> Result<Value, String> {
        let positional = self.positionals.first().map(String::as_str).unwrap_or("");
        match self.command.as_deref().unwrap_or("") {
            "index" => engine.index(&self.positionals, self.force),
            "remove" => engine.remove(positional),
            "detect" => engine.detect(self.scan_new),
            "stats" => Ok(engine.stats()),
            "map" => Ok(engine.map()),
            "project-brief" => Ok(engine.project_brief()),
            "enrichment-plan" => Ok(engine.enrichment_plan()),
            "history" => engine.history(),
            "clear" => engine.clear(),
            "migrate" => engine.migrate(),
            "search" => Ok(engine.search(positional, &self.search_mode, self.top_k)),
            "search-text" => engine.search_text(positional),
            "agent-grep" => engine.agent_grep(positional, self.session.as_deref()),
            "query" => engine.query(positional),
            "find-definition" => Ok(engine.find_definition(positional)),
            "find-references" => Ok(engine.find_references(positional)),
            "impact-radius" => Ok(engine.impact(self.symbol.as_deref().unwrap_or(positional))),
            "coupling" => Ok(engine.coupling(positional)),
            "get-context" => engine.get_context(positional, self.session.as_deref()),
            "init" => init_marker(&engine.cwd),
            "annotations" => engine.annotations(
                self.action.as_deref().unwrap_or("get"),
                positional,
                self.positionals.get(1).map(String::as_str).unwrap_or(""),
            ),
            "document-lock" => engine.document_lock(
                self.action.as_deref().unwrap_or("status"),
                positional,
                self.session.as_deref().unwrap_or("default"),
            ),
            other => Err(format!("unknown command '{other}'")),
        }
    }
}

fn init_marker(cwd: &std::path::Path) -> Result<Value, String> {
    let path = cwd.join(".stele-context.toml");
    if !path.exists() {
        std::fs::write(&path, "# stele-context 2.0\n").map_err(|err| err.to_string())?;
    }
    Ok(Value::object(vec![(
        "path",
        Value::string(path.display().to_string()),
    )]))
}

fn parse(args: &[String]) -> Result<Parsed, String> {
    let mut parsed = Parsed {
        help: false,
        version: false,
        human: false,
        storage: None,
        command: None,
        positionals: Vec::new(),
        search_mode: "keyword".to_string(),
        symbol: None,
        session: None,
        action: None,
        top_k: 10,
        force: false,
        scan_new: true,
    };
    let mut index = 0usize;
    let mut command_seen = false;
    while index < args.len() {
        let arg = &args[index];
        if let Some(value) = arg.strip_prefix("--storage-dir=") {
            parsed.storage = Some(PathBuf::from(value));
            index += 1;
            continue;
        }
        if let Some(value) = arg.strip_prefix("--search-mode=") {
            parsed.search_mode = value.to_string();
            index += 1;
            continue;
        }
        if arg == "--" {
            parsed.positionals.extend(args[index + 1..].iter().cloned());
            break;
        }
        if arg.starts_with('-') && parsed.command.is_none() && !command_seen {
            match arg.as_str() {
                "--version" => parsed.version = true,
                "--help" | "-h" => parsed.help = true,
                "--human" => parsed.human = true,
                "--json" => {}
                "--storage-dir" => {
                    parsed.storage = Some(PathBuf::from(take_value(args, &mut index)?))
                }
                other => return Err(format!("unknown flag '{other}'")),
            }
            index += 1;
            continue;
        }
        if !command_seen && !arg.starts_with('-') {
            parsed.command = Some(arg.clone());
            command_seen = true;
            index += 1;
            continue;
        }
        if arg.starts_with('-') {
            match arg.as_str() {
                "--help" | "-h" => parsed.help = true,
                "--human" => parsed.human = true,
                "--json" => {}
                "--force" | "--force-reindex" => parsed.force = true,
                "--summary-mode" => {}
                "--no-scan-new" => parsed.scan_new = false,
                "--storage-dir" => {
                    parsed.storage = Some(PathBuf::from(take_value(args, &mut index)?))
                }
                "--search-mode" => parsed.search_mode = take_value(args, &mut index)?,
                "--symbol" => parsed.symbol = Some(take_value(args, &mut index)?),
                "--session-id" => parsed.session = Some(take_value(args, &mut index)?),
                "--action" => parsed.action = Some(take_value(args, &mut index)?),
                "--top-k" => {
                    parsed.top_k = take_value(args, &mut index)?
                        .parse()
                        .map_err(|_| "--top-k needs an integer".to_string())?;
                }
                other => return Err(format!("unknown flag '{other}'")),
            }
            index += 1;
            continue;
        }
        parsed.positionals.push(arg.clone());
        index += 1;
    }
    if parsed.command.as_deref() == Some("index") && parsed.positionals.is_empty() {
        parsed.positionals.push(".".to_string());
    }
    Ok(parsed)
}

fn take_value(args: &[String], index: &mut usize) -> Result<String, String> {
    *index += 1;
    args.get(*index)
        .cloned()
        .ok_or_else(|| format!("{} needs a value", args[*index - 1]))
}

fn help_text() -> String {
    format!(
        "\
stele-context {VERSION}

Local context cache for AI coding agents.

Usage:
  stele-context [--storage-dir DIR] [--human] <command>

Commands:
  doctor              Health, storage, and whether a legacy store is present
  index [PATH...]     Chunk and store files. Unchanged files are skipped
  remove PATH         Drop one document from the store
  detect              Compare mtime and size. New files are indexed
  stats               Document, chunk, and symbol counts
  map                 One row per indexed file
  project-brief       The same counts, for the 1.5 command name
  search QUERY        Keyword search. --search-mode hybrid blends cosine
  search-text PATTERN Linear-time search. Lookaround is rejected
  agent-grep PATTERN  The same search, recorded when --session-id is set
  query TEXT          Search, definitions, and text search
  find-definition NAME
  find-references NAME
  impact-radius       --symbol NAME
  coupling PATH
  enrichment-plan     Chunks that still need a summary
  history             Journal of store mutations
  migrate             Copy a 1.5 SQLite store. The original is not modified
  init                Write .stele-context.toml
  serve-mcp           Speak MCP on stdin

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
        let response = dispatch(&["nope".to_string()]);
        assert_eq!(response.code, 1);
        let text = std::str::from_utf8(&response.stdout).unwrap();
        assert!(text.contains("\"ok\":false"));
        assert!(text.contains("unknown command"));
    }

    #[test]
    fn help_mentions_doctor() {
        let response = dispatch(&["--help".to_string()]);
        assert_eq!(response.code, 0);
        let text = std::str::from_utf8(&response.stdout).unwrap();
        assert!(text.contains("doctor"));
        assert!(text.contains("index"));
    }
}
