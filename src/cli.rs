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

pub fn dispatch_in(cwd: PathBuf, args: &[String]) -> Response {
    match parse(args) {
        Ok(mut parsed) => {
            parsed.cwd = Some(cwd);
            parsed.run()
        }
        Err(message) => Response::failure(
            "",
            EXIT_USAGE,
            ErrorBody::new("usage", message, "run stele-context --help"),
        ),
    }
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
    port: u16,
    host: String,
    regex: bool,
    working_tree: bool,
    confirm: bool,
    compact: Option<bool>,
    path_prefix: Option<String>,
    document: Option<String>,
    limit: Option<usize>,
    depth: Option<usize>,
    significance: Option<f64>,
    mode: Option<String>,
    summary_mode: bool,
    chunk_id: Option<String>,
    top_n: Option<usize>,
    min_tokens: Option<usize>,
    max_tokens: Option<i64>,
    group_by: Option<String>,
    no_index: bool,
    cwd: Option<PathBuf>,
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
        let cwd = match self.cwd.clone() {
            Some(path) => path,
            None => match env::current_dir() {
                Ok(path) => path,
                Err(err) => {
                    return Response::failure(
                        &command,
                        EXIT_ERROR,
                        ErrorBody::new("storage", format!("cwd: {err}"), "pass --storage-dir"),
                    );
                }
            },
        };
        let opened = if command == "detect" {
            Engine::open_detect(cwd, self.storage.as_deref())
        } else {
            Engine::open_from(cwd, self.storage.as_deref())
        };
        let mut engine = match opened {
            Ok(engine) => engine,
            Err(message) => {
                return Response::failure(
                    &command,
                    EXIT_ERROR,
                    ErrorBody::new("storage", message, "pass --storage-dir or set HOME"),
                );
            }
        };
        if command == "serve" {
            return match crate::http_serve::listen(engine, &self.host, self.port) {
                Ok(()) => Response::success(
                    "serve",
                    Value::object(vec![("listening", Value::Bool(true))]),
                ),
                Err(message) => Response::failure(
                    "serve",
                    EXIT_ERROR,
                    ErrorBody::new("command_failed", message, "pass --port"),
                ),
            };
        }
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
            "detect" => engine.detect_with(self.scan_new, None, self.limit.unwrap_or(200)),
            "stats" => Ok(engine.stats()),
            "map" => Ok(engine.map()),
            "project-brief" => Ok(engine.project_brief()),
            "enrichment-plan" => Ok(engine.enrichment_plan()),
            "history" => engine.history(),
            "clear" => {
                if !self.confirm {
                    return Ok(Value::object(vec![
                        ("cancelled", Value::Bool(true)),
                        ("reason", Value::string("pass --confirm to clear the store")),
                    ]));
                }
                engine.clear()
            }
            "migrate" => engine.migrate(),
            "search" => {
                if self.working_tree {
                    engine.index_working_tree();
                }
                let mut value = engine.search(positional, &self.search_mode, self.top_k);
                if let Some(prefix) = &self.path_prefix {
                    if let crate::json::Value::Array(rows) = &mut value {
                        rows.retain(|row| {
                            crate::store::string_field(row, "document_path").starts_with(prefix)
                        });
                    }
                }
                if self.compact == Some(true) {
                    if let crate::json::Value::Array(rows) = &mut value {
                        let total = rows.len() as i64;
                        let mut used = 0i64;
                        for row in rows.iter_mut() {
                            let crate::json::Value::Object(pairs) = row else {
                                continue;
                            };
                            let content = pairs.iter().find(|(key, _)| key == "content").and_then(
                                |(_, item)| match item {
                                    crate::json::Value::String(text) => Some(text.clone()),
                                    _ => None,
                                },
                            );
                            let Some(content) = content else {
                                continue;
                            };
                            let preview = if content.chars().count() > 400 {
                                let mut text: String = content.chars().take(400).collect();
                                text.push('\u{2026}');
                                text
                            } else {
                                content.clone()
                            };
                            used += (crate::chunk::estimate_tokens(&preview) + 32) as i64;
                            pairs.retain(|(key, _)| key != "content");
                            pairs.push((
                                "content_preview".to_string(),
                                crate::json::Value::string(preview),
                            ));
                            pairs.push((
                                "content_token_estimate".to_string(),
                                crate::json::Value::Int(
                                    crate::chunk::estimate_tokens(&content) as i64
                                ),
                            ));
                        }
                        value = crate::json::Value::object(vec![
                            (
                                "results",
                                std::mem::replace(&mut value, crate::json::Value::Null),
                            ),
                            (
                                "meta",
                                crate::json::Value::object(vec![
                                    ("estimated_tokens_used", crate::json::Value::Int(used)),
                                    ("returned", crate::json::Value::Int(total)),
                                    ("total_results", crate::json::Value::Int(total)),
                                    ("truncated", crate::json::Value::Bool(false)),
                                ]),
                            ),
                        ]);
                    }
                }
                Ok(value)
            }
            "search-text" => engine.text_search(
                positional,
                self.regex,
                self.document.as_deref(),
                self.limit.unwrap_or(50),
                self.session.as_deref(),
                self.working_tree,
            ),
            "agent-grep" => engine.grep_full(
                positional,
                self.regex,
                self.document.as_deref(),
                true,
                true,
                self.group_by.as_deref().unwrap_or("file"),
                self.max_tokens.unwrap_or(4000),
                true,
                0,
                self.session.as_deref(),
                self.working_tree,
            ),
            "query" => {
                if self.working_tree {
                    engine.index_working_tree();
                }
                engine.query(positional)
            }
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
                self.force,
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
        port: 9876,
        host: "127.0.0.1".to_string(),
        regex: false,
        working_tree: false,
        confirm: false,
        compact: None,
        path_prefix: None,
        document: None,
        limit: None,
        depth: None,
        significance: None,
        mode: None,
        summary_mode: false,
        chunk_id: None,
        top_n: None,
        min_tokens: None,
        max_tokens: None,
        group_by: None,
        no_index: false,
        cwd: None,
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
                "--summary-mode" => parsed.summary_mode = true,
                "--no-summary-mode" => parsed.summary_mode = false,
                "--regex" => parsed.regex = true,
                "--working-tree" => parsed.working_tree = true,
                "--confirm" => parsed.confirm = true,
                "--compact" => parsed.compact = Some(true),
                "--no-compact" => parsed.compact = Some(false),
                "--meta" => {}
                "--no-classify" => {}
                "--no-scope" => {}
                "--no-dedup" => {}
                "--no-index" => parsed.no_index = true,
                "--blocking" => {}
                "--no-scan-new" => parsed.scan_new = false,
                "--scan-new" => parsed.scan_new = true,
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
                "--port" => {
                    parsed.port = take_value(args, &mut index)?
                        .parse()
                        .map_err(|_| "--port needs an integer".to_string())?;
                }
                "--host" => parsed.host = take_value(args, &mut index)?,
                "--path-prefix" => parsed.path_prefix = Some(take_value(args, &mut index)?),
                "--document" | "--document-path" => {
                    parsed.document = Some(take_value(args, &mut index)?)
                }
                "--limit" => {
                    parsed.limit = Some(
                        take_value(args, &mut index)?
                            .parse()
                            .map_err(|_| "--limit needs an integer".to_string())?,
                    );
                }
                "--depth" => {
                    parsed.depth = Some(
                        take_value(args, &mut index)?
                            .parse()
                            .map_err(|_| "--depth needs an integer".to_string())?,
                    );
                }
                "--significance-threshold" => {
                    parsed.significance = Some(
                        take_value(args, &mut index)?
                            .parse()
                            .map_err(|_| "--significance-threshold needs a number".to_string())?,
                    );
                }
                "--mode" | "--group-by" | "--type" => {
                    let value = take_value(args, &mut index)?;
                    if arg == "--group-by" {
                        parsed.group_by = Some(value);
                    } else if arg == "--mode" {
                        parsed.mode = Some(value);
                    }
                }
                "--chunk-id" => parsed.chunk_id = Some(take_value(args, &mut index)?),
                "--top-n" => {
                    parsed.top_n = Some(
                        take_value(args, &mut index)?
                            .parse()
                            .map_err(|_| "--top-n needs an integer".to_string())?,
                    );
                }
                "--min-tokens"
                | "--max-tokens"
                | "--max-result-tokens"
                | "--context-lines"
                | "--max-documents" => {
                    let value: i64 = take_value(args, &mut index)?
                        .parse()
                        .map_err(|_| format!("{arg} needs an integer"))?;
                    if arg == "--max-tokens" {
                        parsed.max_tokens = Some(value);
                    }
                    if arg == "--min-tokens" {
                        parsed.min_tokens = Some(value as usize);
                    }
                }
                "--content" | "--tags" | "--session" => {
                    let value = take_value(args, &mut index)?;
                    if arg == "--session" {
                        parsed.session = Some(value);
                    }
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
  detect              Compare mtime and size. New files are listed
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
  serve               GET /tools, GET /health, POST /call on --port (9876)
  serve-mcp           Speak MCP on stdin

JSON is the default on commands. --human prints text.
--version prints the version line and exits. --help prints this text.
Flags: --storage-dir --json --regex --working-tree --confirm --path-prefix
--limit --document --document-path --session --session-id --depth
--significance-threshold --mode --group-by --summary-mode --top-n --top-k
--min-tokens --max-tokens --max-result-tokens --context-lines --compact
--no-compact --no-classify --no-scope --no-dedup --no-index --scan-new
--no-scan-new --force --search-mode --symbol --port --host --blocking
--chunk-id --max-documents --tags --content --type --meta
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
        assert_eq!(response.stdout, b"stele-context 2.0.1\n");
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
        assert!(rendered.contains("\"version\":\"2.0.1\""));
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
