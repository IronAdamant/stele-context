//! 2.5 fixes from an M5 review of a large Rust workspace, through the real MCP server and CLI: struct
//! fields are symbols (`find_references tasks` answered `not_found`), and
//! `named_in` connects a Rust symbol to the scripts, docs and string
//! literals that name it (a renamed tool broke Python check scripts the
//! compiler never sees).

use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::mpsc::{channel, Receiver};
use std::time::Duration;

const BINARY: &str = env!("CARGO_BIN_EXE_stele-context");

/// The repro project of the brief: four field uses of `Index::tasks`, a tool
/// called by string from a script and named in a doc.
const INDEX: &str = "pub struct Task { pub id: u32 }
pub struct Index {
    pub tasks: Vec<Task>,
    pub notes: Vec<String>,
}
impl Index {
    pub fn add_task(&mut self, t: Task) {
        self.tasks.push(t);
    }
    pub fn drop_task(&mut self, id: u32) {
        self.tasks.retain(|t| t.id != id);
    }
    pub fn count(&self) -> usize { self.tasks.len() + self.notes.len() }
}
pub fn build() -> Index { Index { tasks: Vec::new(), notes: Vec::new() } }
";

const TOOL: &str = "pub fn model_export(flag_wind: bool) -> &'static str {
    if flag_wind { \"model_export --wind\" } else { \"model_export\" }
}
pub fn dispatch(name: &str) -> Option<&'static str> {
    match name { \"model_export\" => Some(model_export(false)), _ => None }
}
/// Reached only by name from scripts/check_export.py.
pub fn tools_catalog() -> u32 { 0 }
";

/// A second type with a field `tasks`, read through receivers whose type
/// the line does not say.
const QUEUE: &str = "pub struct Queue { pub tasks: u32 }
impl Queue {
    pub fn bump(&mut self) { self.tasks += 1; }
}
pub fn peek(q: &Queue, i: &crate::index::Index) -> usize {
    q.tasks as usize + i.tasks.len()
}
pub fn make() -> Queue { Queue { tasks: 0 } }
";

const SCRIPT: &str = "import subprocess
def run():
    return subprocess.run([\"app\", \"call\", \"model_export\", \"--wind\"])
def catalog():
    return call(\"tools_catalog\")
";

const DOC: &str = "# Tools
`model_export` writes a .glb. Pass `--wind` for wind weights.
";

struct Project {
    root: PathBuf,
}

impl Project {
    fn new(tag: &str) -> Project {
        // Tests run in parallel and Windows clocks are coarse: the counter
        // keeps two trees of one process apart.
        static NEXT: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(0);
        let root = std::env::temp_dir().join(format!(
            "stele-r2-{tag}-{}-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, std::sync::atomic::Ordering::Relaxed),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_nanos())
                .unwrap_or(0)
        ));
        for dir in ["src", "scripts", "docs", ".git"] {
            std::fs::create_dir_all(root.join(dir)).unwrap();
        }
        std::fs::write(root.join(".git").join("HEAD"), "ref: refs/heads/main\n").unwrap();
        std::fs::write(root.join("src/index.rs"), INDEX).unwrap();
        std::fs::write(root.join("src/tool.rs"), TOOL).unwrap();
        std::fs::write(root.join("src/queue.rs"), QUEUE).unwrap();
        std::fs::write(
            root.join("src/lib.rs"),
            "pub mod index;\npub mod queue;\npub mod tool;\n",
        )
        .unwrap();
        std::fs::write(root.join("scripts/check_export.py"), SCRIPT).unwrap();
        std::fs::write(root.join("docs/tools.md"), DOC).unwrap();
        let status = Command::new(BINARY)
            .current_dir(&root)
            .args(["index", "."])
            .env_remove("STELE_CONTEXT_STORAGE_DIR")
            .stdout(Stdio::null())
            .status()
            .unwrap();
        assert!(status.success());
        Project { root }
    }

    fn cli(&self, args: &[&str]) -> String {
        let out = Command::new(BINARY)
            .current_dir(&self.root)
            .args(args)
            .env_remove("STELE_CONTEXT_STORAGE_DIR")
            .output()
            .unwrap();
        assert!(out.status.success(), "{args:?}");
        String::from_utf8_lossy(&out.stdout).replace("\\\\", "/")
    }
}

impl Drop for Project {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.root);
    }
}

struct Server {
    child: Child,
    stdin: ChildStdin,
    lines: Receiver<String>,
    next: u64,
}

impl Server {
    fn start(cwd: &Path) -> Server {
        let mut child = Command::new(BINARY)
            .current_dir(cwd)
            .arg("serve-mcp")
            .env_remove("STELE_TOOLS")
            .env_remove("STELE_DISABLE")
            .env_remove("STELE_MCP_MODE")
            .env_remove("STELE_CONTEXT_STORAGE_DIR")
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
        server.request("initialize", r#"{"protocolVersion":"2025-06-18"}"#);
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
                .recv_timeout(Duration::from_secs(120))
                .expect("reply");
            if line.contains(&format!("\"id\":{id},")) {
                return line;
            }
        }
    }

    /// The tool's result text with `/` separators.
    fn call(&mut self, tool: &str, arguments: &str) -> String {
        let line = self.request(
            "tools/call",
            &format!(r#"{{"name":"{tool}","arguments":{arguments}}}"#),
        );
        let start = line.find("\"text\":\"").expect("text") + 8;
        let end = line.rfind("\"}]").expect("end");
        line[start..end]
            .replace("\\\"", "\"")
            .replace("\\\\", "\\")
            .replace("\\\\", "/")
            .replace('\\', "/")
    }
}

impl Drop for Server {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

/// The JSON object that starts at the first `"key":` in `text`.
fn object_after<'a>(text: &'a str, key: &str) -> &'a str {
    let start = text
        .find(&format!("\"{key}\":"))
        .unwrap_or_else(|| panic!("{key}: {text}"));
    let rest = &text[start..];
    let open = rest.find('{').unwrap();
    let mut depth = 0i32;
    for (at, ch) in rest[open..].char_indices() {
        match ch {
            '{' => depth += 1,
            '}' => {
                depth -= 1;
                if depth == 0 {
                    return &rest[open..open + at + 1];
                }
            }
            _ => {}
        }
    }
    rest
}

/// The reference row of `path` at `line`.
fn row<'a>(text: &'a str, path: &str, line: u32) -> &'a str {
    let needle = format!("\"document_path\":\"{path}\",\"line_number\":{line},");
    let at = text
        .find(&needle)
        .unwrap_or_else(|| panic!("{path}:{line}: {text}"));
    let open = text[..at].rfind('{').unwrap();
    let close = at + text[at..].find('}').unwrap();
    &text[open..=close]
}

/// M5, on a large Rust workspace: "find every place a reference is added or dropped" on four
/// fields took five greps; `find_references tasks` said `not_found`.
#[test]
fn a_struct_field_has_its_definition_reads_and_writes() {
    let project = Project::new("fields");
    let mut server = Server::start(&project.root);
    let refs = server.call("find_references", r#"{"symbol":"tasks","owner":"Index"}"#);
    assert!(refs.contains(r#""verdict":"referenced""#), "{refs}");
    assert!(refs.contains(r#""kind":"field""#), "{refs}");
    assert!(refs.contains(r#""line_number":3"#), "{refs}");
    // The two writes, the read and the struct literal, all owned by Index.
    for (line, kind, write) in [
        (8, "field_access", true),
        (11, "field_access", true),
        (13, "field_access", false),
        (15, "struct_field", false),
    ] {
        let found = row(&refs, "src/index.rs", line);
        assert!(found.contains(&format!("\"kind\":\"{kind}\"")), "{found}");
        assert!(found.contains(r#""owner":"Index""#), "{found}");
        assert_eq!(found.contains(r#""write":true"#), write, "{found}");
    }
    // owner=Index leaves out Queue's rows and counts its definition.
    assert!(!refs.contains("src/queue.rs"), "{refs}");
    assert!(refs.contains(r#""other_owner_definitions":1"#), "{refs}");
    let fields = object_after(&refs, "fields");
    assert!(fields.contains(r#""Index":4"#), "{fields}");
}

/// A field name two types share: rows grouped by the owner the line names,
/// the rest marked name_only with the types they may belong to.
#[test]
fn a_shared_field_name_is_grouped_by_owner_and_name_only_rows_say_so() {
    let project = Project::new("shared");
    let mut server = Server::start(&project.root);
    let refs = server.call("find_references", r#"{"symbol":"tasks"}"#);
    let fields = object_after(&refs, "fields");
    assert!(fields.contains(r#""Index":4"#), "{fields}");
    assert!(fields.contains(r#""Queue":2"#), "{fields}");
    assert!(fields.contains(r#""name_only":1"#), "{fields}");
    assert!(fields.contains("any of Index, Queue"), "{fields}");
    // `self.tasks += 1` in impl Queue is a write; `q.tasks` and `i.tasks`
    // on one line give one name_only row.
    let bump = row(&refs, "src/queue.rs", 3);
    assert!(
        bump.contains(r#""owner":"Queue""#) && bump.contains(r#""write":true"#),
        "{bump}"
    );
    let peek = row(&refs, "src/queue.rs", 6);
    assert!(
        peek.contains(r#""name_only":true"#) && !peek.contains("owner"),
        "{peek}"
    );
    // Owned rows come first, grouped by owner: Index's, then Queue's.
    let listed = &refs[refs.find("\"references\"").unwrap()..];
    let index_at = listed.find("\"owner\":\"Index\"").unwrap();
    let queue_at = listed.find("\"owner\":\"Queue\"").unwrap();
    let name_only_at = listed.find("\"name_only\":true").unwrap();
    assert!(index_at < queue_at && queue_at < name_only_at, "{refs}");
    // A plain-word name is not scanned for named_in, and says why.
    let named = object_after(&refs, "named_in");
    assert!(
        named.contains("plain word") && named.contains("named_in=true"),
        "{named}"
    );
    // An owner without the field is an error that lists the ones with it.
    let miss = server.call("find_references", r#"{"symbol":"tasks","owner":"Task"}"#);
    assert!(miss.contains("Types with one: Index, Queue"), "{miss}");
}

/// Field rows past the cap are counted, not listed; full=true lists all.
#[test]
fn field_rows_are_capped_and_counted() {
    let project = Project::new("cap");
    let mut many = String::from("pub struct Wide { pub slot: u32 }\nimpl Wide {\n");
    for n in 0..60 {
        many.push_str(&format!(
            "    pub fn f{n}(&self) -> u32 {{ self.slot + {n} }}\n"
        ));
    }
    many.push_str("}\n");
    std::fs::write(project.root.join("src/wide.rs"), many).unwrap();
    let status = Command::new(BINARY)
        .current_dir(&project.root)
        .args(["index", "src/wide.rs"])
        .env_remove("STELE_CONTEXT_STORAGE_DIR")
        .stdout(Stdio::null())
        .status()
        .unwrap();
    assert!(status.success());
    let mut server = Server::start(&project.root);
    let capped = server.call("find_references", r#"{"symbol":"slot"}"#);
    assert_eq!(
        capped.matches(r#""kind":"field_access""#).count(),
        50,
        "{capped}"
    );
    assert!(capped.contains(r#""omitted":10"#), "{capped}");
    assert!(capped.contains(r#""total":61"#), "{capped}");
    assert!(capped.contains(r#""Wide":60"#), "{capped}");
    let full = server.call("find_references", r#"{"symbol":"slot","full":true}"#);
    assert_eq!(full.matches(r#""kind":"field_access""#).count(), 60);
    let cli = project.cli(&["find-references", "slot", "--limit", "5"]);
    assert_eq!(cli.matches(r#""kind":"field_access""#).count(), 5, "{cli}");
}

/// M5, on a large Rust workspace: two Python check scripts failed after a merge because of a
/// Rust change; nothing connected the Rust symbol to the scripts naming it.
#[test]
fn named_in_lists_scripts_docs_and_string_literals() {
    let project = Project::new("named");
    let mut server = Server::start(&project.root);
    let refs = server.call("find_references", r#"{"symbol":"model_export"}"#);
    assert!(refs.contains(r#""verdict":"referenced""#), "{refs}");
    let named = object_after(&refs, "named_in");
    assert!(named.contains(r#""files_total":3"#), "{named}");
    for (path, line) in [
        ("scripts/check_export.py", 3),
        ("docs/tools.md", 2),
        ("src/tool.rs", 2),
    ] {
        assert!(
            named.contains(&format!(r#""path":"{path}","count":1,"lines":[{line}]"#)),
            "{path}: {named}"
        );
    }
    // Line 5 names it in a string too, but is a call row already.
    assert!(!named.contains("[5]"), "{named}");
    // The verdict does not change; a symbol only scripts name gets a note.
    let only = server.call("find_references", r#"{"symbol":"tools_catalog"}"#);
    assert!(only.contains(r#""verdict":"unreferenced""#), "{only}");
    assert!(only.contains("a dispatch by string may reach it"), "{only}");
    assert!(
        object_after(&only, "named_in").contains(r#""path":"scripts/check_export.py""#),
        "{only}"
    );
    // impact_radius names the same files: what a rename breaks unseen.
    let impact = server.call("impact_radius", r#"{"symbol":"model_export"}"#);
    let named = object_after(&impact, "named_in");
    assert!(named.contains("scripts/check_export.py"), "{named}");
    let off = server.call(
        "find_references",
        r#"{"symbol":"model_export","named_in":false}"#,
    );
    assert!(!off.contains("named_in"), "{off}");
    // The CLI answers the same.
    let cli = project.cli(&["find-references", "model_export"]);
    assert!(cli.contains("scripts/check_export.py"), "{cli}");
    let forced = project.cli(&["find-references", "tasks", "--named-in"]);
    assert!(
        object_after(&forced, "named_in").contains("files_total"),
        "{forced}"
    );
}
