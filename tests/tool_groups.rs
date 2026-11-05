//! Tool groups against the real server binary: STELE_TOOLS / STELE_DISABLE
//! and the `agent-tools` block of the project's AGENTS.md.

use std::io::{BufRead, BufReader, Write};
use std::path::PathBuf;
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::mpsc::{channel, Receiver};
use std::time::Duration;

const BINARY: &str = env!("CARGO_BIN_EXE_stele-context");

/// A scratch project (with a root marker) and its store, removed on drop.
struct Project {
    dir: PathBuf,
}

impl Project {
    fn new(agents_md: Option<&str>) -> Project {
        let dir = std::env::temp_dir().join(format!(
            "stele-groups-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_nanos())
                .unwrap_or(0)
        ));
        std::fs::create_dir_all(&dir).expect("project dir");
        std::fs::write(dir.join(".stele-context.toml"), "").expect("marker");
        if let Some(text) = agents_md {
            std::fs::write(dir.join("AGENTS.md"), text).expect("AGENTS.md");
        }
        Project { dir }
    }

    fn command(&self, vars: &[(&str, &str)]) -> Command {
        let mut command = Command::new(BINARY);
        command
            .current_dir(&self.dir)
            .arg("--storage-dir")
            .arg(self.dir.join(".store"))
            .arg("serve-mcp")
            .env_remove("STELE_TOOLS")
            .env_remove("STELE_DISABLE")
            .env_remove("STELE_MCP_MODE")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        for (key, value) in vars {
            command.env(key, value);
        }
        command
    }

    /// Send all `requests`, close stdin, and return stdout lines, stderr
    /// and the exit code.
    fn session(&self, vars: &[(&str, &str)], requests: &[&str]) -> (Vec<String>, String, i32) {
        let mut child = self.command(vars).spawn().expect("spawn server");
        {
            let mut stdin = child.stdin.take().expect("stdin");
            for line in requests {
                // The server may already have exited (exit 2 cases).
                let _ = writeln!(stdin, "{line}");
            }
        }
        let stdout = child.stdout.take().expect("stdout");
        let lines: Vec<String> = BufReader::new(stdout)
            .lines()
            .map_while(Result::ok)
            .collect();
        let output = child.wait_with_output().expect("wait");
        (
            lines,
            String::from_utf8_lossy(&output.stderr).to_string(),
            output.status.code().unwrap_or(-1),
        )
    }
}

impl Drop for Project {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.dir);
    }
}

fn session(vars: &[(&str, &str)], requests: &[&str]) -> (Vec<String>, String, i32) {
    Project::new(None).session(vars, requests)
}

/// A running server driven one request at a time.
struct Live {
    child: Child,
    stdin: ChildStdin,
    lines: Receiver<String>,
}

impl Live {
    fn start(project: &Project, vars: &[(&str, &str)]) -> Live {
        let mut child = project.command(vars).spawn().expect("spawn server");
        let stdin = child.stdin.take().expect("stdin");
        let stdout = child.stdout.take().expect("stdout");
        let (sender, lines) = channel();
        std::thread::spawn(move || {
            for line in BufReader::new(stdout).lines().map_while(Result::ok) {
                if sender.send(line).is_err() {
                    break;
                }
            }
        });
        Live {
            child,
            stdin,
            lines,
        }
    }

    /// Send one request with `id` and return every line up to its reply.
    fn ask(&mut self, id: u32, request: &str) -> Vec<String> {
        writeln!(self.stdin, "{request}").expect("write");
        self.stdin.flush().expect("flush");
        let mut out = Vec::new();
        loop {
            let line = self
                .lines
                .recv_timeout(Duration::from_secs(60))
                .expect("reply");
            let done = line.contains(&format!("\"id\":{id},"));
            out.push(line);
            if done {
                return out;
            }
        }
    }
}

impl Drop for Live {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

const INIT: &str = r#"{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"t","version":"0"}}}"#;
const LIST: &str = r#"{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}"#;
const CHANGED: &str = r#"{"jsonrpc":"2.0","method":"notifications/tools/list_changed"}"#;

fn list(id: u32) -> String {
    format!(r#"{{"jsonrpc":"2.0","id":{id},"method":"tools/list","params":{{}}}}"#)
}

fn call(id: u32, name: &str, arguments: &str) -> String {
    format!(
        r#"{{"jsonrpc":"2.0","id":{id},"method":"tools/call","params":{{"name":"{name}","arguments":{arguments}}}}}"#
    )
}

fn listed(line: &str) -> Vec<String> {
    line.split("\"name\":\"")
        .skip(1)
        .filter_map(|rest| rest.split('"').next())
        .map(str::to_string)
        .collect()
}

fn has(names: &[String], name: &str) -> bool {
    names.iter().any(|item| item == name)
}

fn lite_reply(id: u32) -> String {
    format!(
        "{{\"jsonrpc\":\"2.0\",\"id\":{id},\"result\":{}}}",
        include_str!("../src/mcp/tools_lite.json")
    )
}

#[test]
fn default_listing_is_byte_identical() {
    for (mode, file) in [
        ("lite", include_str!("../src/mcp/tools_lite.json")),
        ("standard", include_str!("../src/mcp/tools_standard.json")),
        ("full", include_str!("../src/mcp/tools_full.json")),
    ] {
        let (lines, _, code) = session(&[("STELE_MCP_MODE", mode)], &[INIT, LIST]);
        assert_eq!(code, 0);
        assert_eq!(
            lines[1],
            format!("{{\"jsonrpc\":\"2.0\",\"id\":2,\"result\":{file}}}")
        );
        // Always advertised: AGENTS.md can change the list mid-session.
        assert!(
            lines[0].contains("\"capabilities\":{\"tools\":{\"listChanged\":true}}"),
            "{}",
            lines[0]
        );
        assert!(
            lines[0].contains("\"instructions\":\"Tool groups on: core, index, search, symbols"),
            "{}",
            lines[0]
        );
        assert!(
            lines[0].contains(
                "; off: none. Change them for this project with a \\\"stele: disable <groups>\\\""
            ),
            "{}",
            lines[0]
        );
    }
}

#[test]
fn tools_and_disable_filter_by_group_and_name() {
    let (lines, _, _) = session(&[("STELE_TOOLS", "search, find_definition")], &[INIT, LIST]);
    let names = listed(&lines[1]);
    assert!(has(&names, "query"));
    assert!(has(&names, "find_definition"));
    assert!(has(&names, "doctor"), "core stays on");
    assert!(!has(&names, "find_references"));
    assert!(!has(&names, "document_lock"));
    assert!(
        lines[0].contains("\"instructions\":\"Tool groups on: core, search, symbols; off: index, embeddings, notes, locks (set by STELE_TOOLS=search,find_definition)."),
        "{}",
        lines[0]
    );

    let (lines, _, _) = session(
        &[
            ("STELE_MCP_MODE", "full"),
            ("STELE_DISABLE", "LOCKS, notes,query"),
        ],
        &[INIT, LIST],
    );
    let names = listed(&lines[1]);
    for gone in [
        "document_lock",
        "acquire_document_lock",
        "list_agents",
        "annotations",
        "annotate",
        "query",
    ] {
        assert!(!has(&names, gone), "{gone}");
    }
    for kept in ["search", "get_notifications", "save_kv_state", "doctor"] {
        assert!(has(&names, kept), "{kept}");
    }
}

#[test]
fn disabled_tool_returns_the_error_line() {
    let lock = call(
        3,
        "document_lock",
        r#"{"action":"status","document_path":"a.py"}"#,
    );
    let (lines, _, _) = session(&[("STELE_DISABLE", "locks")], &[INIT, &lock]);
    let reply = &lines[1];
    assert!(reply.contains("\"isError\":true"), "{reply}");
    assert!(
        reply.contains(r#""text":"tool \"document_lock\" is disabled (group \"locks\") by STELE_DISABLE; remove \"locks\" from it to enable""#),
        "{reply}"
    );
    // batch does not get around it.
    let batch = call(
        4,
        "batch",
        r#"{"operations":[{"method":"document_lock","params":{"action":"status","document_path":"a.py"}}]}"#,
    );
    let (lines, _, _) = session(
        &[("STELE_DISABLE", "locks"), ("STELE_MCP_MODE", "standard")],
        &[INIT, &batch],
    );
    assert!(
        lines[1].contains(r#"tool \\\"document_lock\\\" is disabled (group \\\"locks\\\")"#),
        "{}",
        lines[1]
    );
}

#[test]
fn unknown_name_exits_2_with_the_message() {
    let (lines, stderr, code) = session(&[("STELE_DISABLE", "lokcs")], &[INIT]);
    assert_eq!(code, 2);
    assert!(lines.is_empty());
    let line = stderr.lines().next().unwrap_or("");
    assert!(
        line.starts_with("STELE_DISABLE: unknown group \"lokcs\"; groups: core, index, search, symbols, embeddings, notes, locks, messages, kv; tools: "),
        "{line}"
    );
    assert_eq!(stderr.lines().count(), 1);
}

#[test]
fn core_cannot_be_disabled() {
    let (lines, stderr, code) = session(&[("STELE_DISABLE", "core")], &[INIT, LIST]);
    assert_eq!(code, 2);
    assert!(lines.is_empty());
    assert_eq!(
        stderr.trim_end(),
        "STELE_DISABLE: group \"core\" is always on and cannot be disabled; remove \"core\" from it"
    );
    let (_, stderr, code) = session(&[("STELE_DISABLE", "doctor")], &[INIT]);
    assert_eq!(code, 2);
    assert_eq!(
        stderr.trim_end(),
        "STELE_DISABLE: tool \"doctor\" is in group \"core\", which is always on; remove \"doctor\" from it"
    );
    let (lines, _, code) = session(&[("STELE_TOOLS", "locks")], &[INIT, LIST]);
    assert_eq!(code, 0);
    let names = listed(&lines[1]);
    for kept in ["doctor", "map", "get_context", "document_lock"] {
        assert!(has(&names, kept), "{kept}");
    }
    assert!(!has(&names, "search"));
}

#[test]
fn blank_values_are_unset() {
    let (lines, _, code) = session(
        &[("STELE_TOOLS", ""), ("STELE_DISABLE", "  ")],
        &[INIT, LIST],
    );
    assert_eq!(code, 0);
    assert_eq!(lines[1], lite_reply(2));
}

#[test]
fn agents_md_block_sets_groups_and_env_wins_per_key() {
    let project = Project::new(Some(
        "# Agents\n\n```agent-tools\n# one agent works here\nchisel: disable locks, history\nstele: disable locks notes\nstele: tools search symbols locks\n```\n",
    ));
    let (lines, _, code) = project.session(&[], &[INIT, LIST]);
    assert_eq!(code, 0);
    let names = listed(&lines[1]);
    assert!(has(&names, "query") && has(&names, "find_references"));
    assert!(!has(&names, "document_lock"), "disable line");
    assert!(!has(&names, "index"), "tools line");
    assert!(
        lines[0].contains("(set by AGENTS.md stele: tools search, symbols, locks; AGENTS.md stele: disable locks, notes)."),
        "{}",
        lines[0]
    );
    // STELE_DISABLE replaces the disable line only; the tools line stays.
    let (lines, _, _) = project.session(&[("STELE_DISABLE", "kv")], &[INIT, LIST]);
    let names = listed(&lines[1]);
    assert!(has(&names, "document_lock"));
    assert!(!has(&names, "index"));
    // STELE_TOOLS=all replaces the tools line only.
    let (lines, _, _) = project.session(&[("STELE_TOOLS", "all")], &[INIT, LIST]);
    let names = listed(&lines[1]);
    assert!(has(&names, "index"));
    assert!(!has(&names, "document_lock"));
    let lock = call(
        3,
        "document_lock",
        r#"{"action":"status","document_path":"a.py"}"#,
    );
    let (lines, _, _) = project.session(&[], &[INIT, &lock]);
    assert!(
        lines[1].contains(r#"tool \"document_lock\" is disabled (group \"locks\") by the \"stele: disable\" line in AGENTS.md; remove \"locks\" from it to enable"#),
        "{}",
        lines[1]
    );
}

#[test]
fn agents_md_errors_are_warnings_not_failures() {
    let project = Project::new(Some(
        "```agent-tools\nstele: disable lokcs\nstele: disable core\n```\n",
    ));
    let doctor = call(3, "doctor", "{}");
    let (lines, stderr, code) = project.session(&[], &[INIT, LIST, &doctor]);
    assert_eq!(code, 0, "{stderr}");
    assert_eq!(lines[1], lite_reply(2), "bad lines are ignored");
    assert!(
        lines[0].contains(r#""instructions":"AGENTS.md agent-tools: unknown group \"lokcs\" for stele; groups: core, index, search, symbols, embeddings, notes, locks, messages, kv; line \"stele: disable lokcs\" ignored\nAGENTS.md agent-tools: group \"core\" is always on and cannot be disabled for stele; line \"stele: disable core\" ignored\nTool groups on: core,"#),
        "{}",
        lines[0]
    );
    assert!(lines[2].contains("tool_groups_warning"), "{}", lines[2]);
    // No AGENTS.md, or no block: no warning and the default list.
    let plain = Project::new(Some("# Agents\n\nNo block.\n"));
    let (lines, _, _) = plain.session(&[], &[INIT, LIST, &doctor]);
    assert!(!lines[0].contains("AGENTS.md agent-tools:"));
    assert_eq!(lines[1], lite_reply(2));
    assert!(!lines[2].contains("tool_groups"), "{}", lines[2]);
}

#[test]
fn editing_agents_md_mid_session_changes_the_list() {
    let project = Project::new(None);
    let mut live = Live::start(&project, &[]);
    let first = live.ask(1, INIT);
    assert_eq!(first.len(), 1);
    let before = live.ask(2, &list(2));
    assert_eq!(before, vec![lite_reply(2)]);

    std::fs::write(
        project.dir.join("AGENTS.md"),
        "```agent-tools\nstele: disable locks\n```\n",
    )
    .expect("write AGENTS.md");
    let after = live.ask(3, &list(3));
    assert_eq!(after.len(), 2, "{after:?}");
    assert_eq!(after[0], CHANGED);
    let names = listed(&after[1]);
    assert!(!has(&names, "document_lock"));
    assert!(has(&names, "query"));

    // Nothing changed: no notification.
    let again = live.ask(4, &list(4));
    assert_eq!(again.len(), 1);

    // A change that does not change the exposed set: no notification.
    std::fs::write(
        project.dir.join("AGENTS.md"),
        "# edited\n```agent-tools\nstele: disable locks\n```\n",
    )
    .expect("rewrite AGENTS.md");
    let same = live.ask(5, &list(5));
    assert_eq!(same.len(), 1, "{same:?}");

    // A bad line puts the default back and doctor carries the warning.
    std::fs::write(
        project.dir.join("AGENTS.md"),
        "```agent-tools\nstele: disable lokcs, locks\n```\n",
    )
    .expect("break AGENTS.md");
    let doctor = live.ask(6, &call(6, "doctor", "{}"));
    assert_eq!(doctor[0], CHANGED);
    assert!(doctor[1].contains("unknown group"), "{}", doctor[1]);
    let restored = live.ask(7, &list(7));
    assert_eq!(restored, vec![lite_reply(7)]);
}

/// Prints the tools/list size with everything on and with each group that
/// overlaps another suite tool switched off.
#[test]
fn report_tools_list_sizes() {
    for mode in ["lite", "standard", "full"] {
        let mut row = vec![];
        for disable in ["", "locks", "notes", "messages", "locks,notes,messages"] {
            let mut vars = vec![("STELE_MCP_MODE", mode)];
            if !disable.is_empty() {
                vars.push(("STELE_DISABLE", disable));
            }
            let (lines, _, _) = session(&vars, &[INIT, LIST]);
            row.push(format!(
                "{}={}",
                if disable.is_empty() { "all" } else { disable },
                lines[1].len()
            ));
        }
        println!("tools/list bytes {mode}: {}", row.join(" "));
    }
}
