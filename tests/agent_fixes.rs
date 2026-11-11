//! 2.2 agent-effectiveness fixes, through the real MCP server: tool
//! schemas in every mode, locks that never block their own agent, and
//! compact results with `full` on request.

use std::io::{BufRead, BufReader, Write};
use std::path::PathBuf;
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::mpsc::{channel, Receiver};
use std::time::Duration;

const BINARY: &str = env!("CARGO_BIN_EXE_stele-context");

struct Project {
    dir: PathBuf,
}

impl Project {
    fn new() -> Project {
        let dir = std::env::temp_dir().join(format!(
            "stele-agent-fixes-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_nanos())
                .unwrap_or(0)
        ));
        std::fs::create_dir_all(dir.join("src")).unwrap();
        std::fs::write(dir.join(".stele-context.toml"), "").unwrap();
        // One large definition: its chunk is the whole impl block.
        let mut big = String::from("pub struct Engine;\n\nimpl Engine {\n    /// Walks the tree.\n    pub fn detect_with(&mut self, limit: usize) -> usize {\n");
        for n in 0..1500 {
            big.push_str(&format!("        let value_{n} = limit + {n};\n"));
        }
        big.push_str("        limit\n    }\n}\n");
        std::fs::write(dir.join("src/engine.rs"), big).unwrap();
        std::fs::write(
            dir.join("src/other.rs"),
            "pub fn helper() -> usize {\n    1\n}\n",
        )
        .unwrap();
        Project { dir }
    }
}

impl Drop for Project {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.dir);
    }
}

struct Server {
    child: Child,
    stdin: ChildStdin,
    lines: Receiver<String>,
    next: u64,
}

impl Server {
    fn start(project: &Project, mode: &str) -> Server {
        let mut child = Command::new(BINARY)
            .current_dir(&project.dir)
            .arg("--storage-dir")
            .arg(project.dir.join(".store"))
            .arg("serve-mcp")
            .env("STELE_MCP_MODE", mode)
            .env_remove("STELE_TOOLS")
            .env_remove("STELE_DISABLE")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .expect("spawn server");
        let stdin = child.stdin.take().unwrap();
        let stdout = child.stdout.take().unwrap();
        let (sender, lines) = channel();
        std::thread::spawn(move || {
            for line in BufReader::new(stdout).lines().map_while(Result::ok) {
                if sender.send(line).is_err() {
                    break;
                }
            }
        });
        let mut server = Server {
            child,
            stdin,
            lines,
            next: 0,
        };
        server.request(
            "initialize",
            r#"{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"t","version":"0"}}"#,
        );
        server
    }

    fn request(&mut self, method: &str, params: &str) -> String {
        self.next += 1;
        let id = self.next;
        writeln!(
            self.stdin,
            r#"{{"jsonrpc":"2.0","id":{id},"method":"{method}","params":{params}}}"#
        )
        .unwrap();
        self.stdin.flush().unwrap();
        loop {
            let line = self
                .lines
                .recv_timeout(Duration::from_secs(60))
                .expect("reply");
            if line.contains(&format!("\"id\":{id},")) {
                return line;
            }
        }
    }

    /// The tool's result text (the JSON the agent reads).
    fn call(&mut self, tool: &str, arguments: &str) -> String {
        let line = self.request(
            "tools/call",
            &format!(r#"{{"name":"{tool}","arguments":{arguments}}}"#),
        );
        let start = line.find("\"text\":\"").expect("text") + 8;
        let end = line.rfind("\"}]").expect("end");
        line[start..end].replace("\\\"", "\"").replace("\\\\", "\\")
    }
}

impl Drop for Server {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

/// Every tool in every mode lists its arguments, with the required ones,
/// and lite stays small.
#[test]
fn every_mode_lists_real_argument_schemas() {
    let project = Project::new();
    for (mode, count, max_bytes) in [
        ("lite", 25, 10_000),
        ("standard", 43, 14_000),
        ("full", 61, 19_000),
    ] {
        let mut server = Server::start(&project, mode);
        let listed = server.request("tools/list", "{}");
        assert_eq!(listed.matches("\"inputSchema\"").count(), count, "{mode}");
        assert!(!listed.contains("\"parameters\""), "{mode}");
        assert!(
            !listed.contains("\"description\":\"stele-context "),
            "{mode}"
        );
        assert!(
            listed.contains(r#"{"name":"find_definition","description":"Beats grep for"#),
            "{mode}"
        );
        assert!(listed.contains(r#""required":["symbol"]"#), "{mode}");
        assert!(
            listed.contains(r#""action":{"type":"string","enum":["acquire","release""#),
            "{mode}"
        );
        assert!(listed.len() < max_bytes, "{mode}: {} bytes", listed.len());
        println!("tools/list {mode}: {} bytes", listed.len());
    }
}

/// The agent behind a server is never blocked by the locks its own index
/// call took, and another agent is not blocked by them either; explicit
/// locks still hold.
#[test]
fn index_locks_never_block_an_explicit_acquire() {
    let project = Project::new();
    let mut a = Server::start(&project, "lite");
    let indexed = a.call("index", r#"{"paths":["."]}"#);
    assert!(indexed.contains("\"indexed_count\":2"), "{indexed}");
    let status = a.call(
        "document_lock",
        r#"{"action":"status","document_path":"src/engine.rs"}"#,
    );
    assert!(status.contains("\"auto\":true"), "{status}");
    let mine = a.call(
        "document_lock",
        r#"{"action":"acquire","document_path":"src/engine.rs","agent_id":"agent-a"}"#,
    );
    assert!(mine.contains("\"acquired\":true"), "{mine}");
    // A second agent in its own server.
    let mut b = Server::start(&project, "lite");
    let other = b.call(
        "document_lock",
        r#"{"action":"acquire","document_path":"src/other.rs","agent_id":"agent-b"}"#,
    );
    assert!(other.contains("\"acquired\":true"), "{other}");
    let refused = b.call(
        "document_lock",
        r#"{"action":"acquire","document_path":"src/engine.rs","agent_id":"agent-b"}"#,
    );
    assert!(refused.contains("\"acquired\":false"), "{refused}");
    assert!(refused.contains("agent-a"), "{refused}");
    // Each server sees what the other wrote, and the CLI runs while both
    // are up (2.1 held the store for the server's whole life).
    let note = a.call(
        "annotations",
        r#"{"action":"create","target":"src/engine.rs","target_type":"document","content":"handoff from a"}"#,
    );
    assert!(note.contains("\"id\""), "{note}");
    let seen = b.call(
        "annotations",
        r#"{"action":"get","target":"src/engine.rs","target_type":"document"}"#,
    );
    assert!(seen.contains("handoff from a"), "{seen}");
    let cli = Command::new(BINARY)
        .current_dir(&project.dir)
        .arg("--storage-dir")
        .arg(project.dir.join(".store"))
        .arg("stats")
        .output()
        .expect("cli");
    assert!(cli.status.success());
    assert!(String::from_utf8_lossy(&cli.stdout).contains("\"ok\":true"));
    // Re-indexing does not take agent-a's lock away.
    let forced = a.call(
        "index",
        r#"{"paths":["src/engine.rs"],"force_reindex":true}"#,
    );
    assert!(forced.contains("\"indexed_count\":1"), "{forced}");
    let held = b.call(
        "document_lock",
        r#"{"action":"status","document_path":"src/engine.rs"}"#,
    );
    assert!(held.contains("\"locked_by\":\"agent-a\""), "{held}");
}

/// find_definition answers with the location, signature and a preview;
/// the chunk text only with full=true.
#[test]
fn find_definition_is_compact_and_full_on_request() {
    let project = Project::new();
    let mut server = Server::start(&project, "lite");
    server.call("index", r#"{"paths":["."]}"#);
    let compact = server.call("find_definition", r#"{"symbol":"detect_with"}"#);
    let full = server.call("find_definition", r#"{"symbol":"detect_with","full":true}"#);
    println!(
        "find_definition bytes: compact {} full {}",
        compact.len(),
        full.len()
    );
    assert!(
        compact
            .contains("\"signature\":\"pub fn detect_with(&mut self, limit: usize) -> usize {\""),
        "{compact}"
    );
    assert!(!compact.contains("\"content\""), "{compact}");
    assert!(compact.len() < 1_500, "{}", compact.len());
    assert!(full.contains("\"content\""));
    assert!(full.len() > 30_000, "{}", full.len());
    // Re-indexing an unchanged tree lists counts, not every file.
    let again = server.call("index", r#"{"paths":["."]}"#);
    assert!(again.contains("\"unchanged_count\":2"), "{again}");
    assert!(!again.contains("Unchanged"), "{again}");
    let listed = server.call("index", r#"{"paths":["."],"full":true}"#);
    assert!(listed.contains("Unchanged"), "{listed}");
}
