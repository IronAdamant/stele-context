//! 2.4 fixes from Specula's M5 lanes, through the real MCP server: Rust
//! calls and macros as symbols (the false "unreferenced"), definition-first
//! search without edge blocks, the bulk_store_summaries schema, and
//! `worktree_root` answers from a git worktree.

use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::mpsc::{channel, Receiver};
use std::time::Duration;

const BINARY: &str = env!("CARGO_BIN_EXE_stele-context");

/// A main checkout (`.git` directory) and a linked worktree of it (`.git`
/// file to `.git/worktrees/wt`, whose `commondir` leads back), laid out the
/// way git does without running git.
struct Trees {
    root: PathBuf,
    main: PathBuf,
    tree: PathBuf,
}

const LIB: &str = "pub struct Event;\n\
\n\
fn human_took_controls(input: &[Event]) -> bool {\n\
    !input.is_empty()\n\
}\n\
\n\
pub fn game_frame(input: &[Event], follow: Option<u32>) -> bool {\n\
    if follow.is_some() && human_took_controls(input) {\n\
        return true;\n\
    }\n\
    let took = |events: &[Event]| human_took_controls(events);\n\
    took(input)\n\
}\n\
\n\
#[macro_export]\n\
macro_rules! export_module {\n\
    ($name:ident) => {\n\
        pub fn $name() {}\n\
    };\n\
}\n\
\n\
export_module!(exported);\n";

const GAME: &str = "/// A game.\n\
pub trait Game {\n\
    fn name(&self) -> &str;\n\
    fn use_assets(&mut self, _root: &std::path::Path) {}\n\
}\n";

impl Trees {
    fn new(tag: &str) -> Trees {
        let root = std::env::temp_dir().join(format!(
            "stele-m5-{tag}-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_nanos())
                .unwrap_or(0)
        ));
        let main = root.join("main");
        let tree = root.join("wt");
        let gitdir = main.join(".git").join("worktrees").join("wt");
        std::fs::create_dir_all(&gitdir).unwrap();
        std::fs::write(main.join(".git").join("HEAD"), "ref: refs/heads/main\n").unwrap();
        std::fs::write(gitdir.join("commondir"), "../..\n").unwrap();
        std::fs::create_dir_all(main.join("src")).unwrap();
        std::fs::write(main.join("src/lib.rs"), LIB).unwrap();
        std::fs::write(main.join("src/game.rs"), GAME).unwrap();
        // Mentions Game and use_assets many times, defines neither.
        let mut noise = String::from("use crate::game::Game;\n");
        for n in 0..40 {
            noise.push_str(&format!(
                "// Game trait: use_assets runs before step {n}; Game use_assets Game\n"
            ));
        }
        noise.push_str(
            "pub fn sim_writes(g: &mut dyn Game) { g.use_assets(std::path::Path::new(\".\")); }\n",
        );
        std::fs::write(main.join("src/sim_writes.rs"), noise).unwrap();
        std::fs::write(main.join("src/old.rs"), "pub fn retired_helper() {}\n").unwrap();
        std::fs::create_dir_all(tree.join("src")).unwrap();
        for name in ["lib.rs", "game.rs", "sim_writes.rs", "old.rs"] {
            std::fs::copy(main.join("src").join(name), tree.join("src").join(name)).unwrap();
        }
        std::fs::write(tree.join(".git"), format!("gitdir: {}\n", gitdir.display())).unwrap();
        let status = Command::new(BINARY)
            .current_dir(&main)
            .args(["index", "."])
            .stdout(Stdio::null())
            .status()
            .unwrap();
        assert!(status.success());
        Trees { root, main, tree }
    }
}

impl Drop for Trees {
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

fn json_path(path: &Path) -> String {
    path.display().to_string().replace('\\', "\\\\")
}

/// Specula M5: `find_references human_took_controls` answered
/// "unreferenced" for a private fn called by bare name in the same file.
#[test]
fn a_private_fn_called_by_name_is_referenced_on_every_line() {
    let trees = Trees::new("refs");
    let mut server = Server::start(&trees.main);
    let refs = server.call("find_references", r#"{"symbol":"human_took_controls"}"#);
    assert!(refs.contains(r#""verdict":"referenced""#), "{refs}");
    // Called on lines 8 (inside `if ... &&`) and 11 (in a closure).
    for line in [8, 11] {
        assert!(
            refs.contains(&format!("\"line_number\":{line}")),
            "{line}: {refs}"
        );
    }
    assert!(refs.contains(r#""kind":"call""#), "{refs}");
}

/// `macro_rules!` definitions are symbols, and `name!(..)` is a reference.
#[test]
fn macro_rules_definitions_and_invocations() {
    let trees = Trees::new("macro");
    let mut server = Server::start(&trees.main);
    let def = server.call("find_definition", r#"{"symbol":"export_module"}"#);
    assert!(def.contains(r#""kind":"macro""#), "{def}");
    assert!(def.contains(r#""line_number":16"#), "{def}");
    assert!(def.contains("macro_rules! export_module"), "{def}");
    let refs = server.call("find_references", r#"{"symbol":"export_module"}"#);
    assert!(refs.contains(r#""verdict":"referenced""#), "{refs}");
    assert!(refs.contains(r#""line_number":22"#), "{refs}");
}

/// Specula M5: "Game trait use_assets" ranked an incidental file first and
/// carried about 60 edges. The defining chunk comes first, without edges
/// unless asked.
#[test]
fn search_ranks_the_definition_first_without_edges() {
    let trees = Trees::new("search");
    let mut server = Server::start(&trees.main);
    let hits = server.call("search", r#"{"query":"Game trait use_assets","top_k":3}"#);
    let first = hits.find("\"document_path\"").expect("a row");
    let first_path: String = hits[first..].chars().take(40).collect();
    assert!(first_path.contains("game.rs"), "{hits}");
    assert!(hits.contains(r#""source":"definition""#), "{hits}");
    assert!(
        hits.contains(r#""defines":["Game","use_assets"]"#),
        "{hits}"
    );
    assert!(!hits.contains("\"edges\""), "{hits}");
    let with = server.call(
        "search",
        r#"{"query":"Game trait use_assets","top_k":3,"include_edges":true}"#,
    );
    assert!(with.contains("\"edges\""), "{with}");
    let old = server.call(
        "search",
        r#"{"query":"Game trait use_assets","top_k":3,"definitions_first":false}"#,
    );
    assert!(!old.contains(r#""source":"definition""#), "{old}");
}

/// The Anthropic API rejected `"type": "object of chunk_id to string"`.
#[test]
fn bulk_store_summaries_has_a_valid_schema_and_descriptions_say_when() {
    let trees = Trees::new("schema");
    let mut server = Server::start(&trees.main);
    let listed = server.request("tools/list", "{}");
    assert!(
        listed
            .contains(r#""summaries":{"type":"object","additionalProperties":{"type":"string"}}"#),
        "{listed}"
    );
    assert!(!listed.contains("object of chunk_id"), "{listed}");
    assert!(listed.contains("Beats grep"), "{listed}");
    assert!(
        listed.contains(r#""worktree_root":{"type":"string"}"#),
        "{listed}"
    );
    let init = server.request("initialize", r#"{"protocolVersion":"2025-06-18"}"#);
    assert!(init.contains("Stele beats grep for"), "{init}");
    assert!(init.contains("worktree_root"), "{init}");
}

/// `worktree_root`: main index plus an overlay of the worktree's edited,
/// added and deleted files; the main index answers unchanged without it.
#[test]
fn worktree_root_answers_for_the_worktree() {
    let trees = Trees::new("wt");
    std::fs::write(
        trees.tree.join("src/lib.rs"),
        LIB.replace("human_took_controls", "human_took_controls_wt"),
    )
    .unwrap();
    std::fs::write(
        trees.tree.join("src/added.rs"),
        "pub fn added_in_worktree() -> u32 {\n    7\n}\n",
    )
    .unwrap();
    std::fs::remove_file(trees.tree.join("src/old.rs")).unwrap();
    let snapshot = trees.main.join(".stele-context/store/snapshot.json");
    let before = std::fs::read(&snapshot).unwrap();
    let mut server = Server::start(&trees.main);
    let wt = json_path(&trees.tree);

    let plain = server.call("find_definition", r#"{"symbol":"retired_helper"}"#);
    assert!(plain.contains(r#""count":1"#), "{plain}");
    assert!(!plain.contains("\"worktree\""), "{plain}");

    let added = server.call(
        "find_definition",
        &format!(r#"{{"symbol":"added_in_worktree","worktree_root":"{wt}"}}"#),
    );
    assert!(added.contains(r#""count":1"#), "{added}");
    assert!(added.contains(r#""overlay_files":3"#), "{added}");
    assert!(added.contains("\"worktree\":\""), "{added}");
    assert!(added.contains("added.rs"), "{added}");

    let deleted = server.call(
        "find_definition",
        &format!(r#"{{"symbol":"retired_helper","worktree_root":"{wt}"}}"#),
    );
    assert!(deleted.contains(r#""count":0"#), "{deleted}");

    let edited = server.call(
        "find_references",
        &format!(r#"{{"symbol":"human_took_controls_wt","worktree_root":"{wt}"}}"#),
    );
    assert!(edited.contains(r#""verdict":"referenced""#), "{edited}");
    let renamed = server.call(
        "find_references",
        &format!(r#"{{"symbol":"human_took_controls","worktree_root":"{wt}"}}"#),
    );
    assert!(renamed.contains(r#""verdict":"not_found""#), "{renamed}");

    // An array reply is wrapped with the metadata.
    let hits = server.call(
        "search",
        &format!(r#"{{"query":"added_in_worktree","worktree_root":"{wt}"}}"#),
    );
    assert!(hits.starts_with(r#"{"results":["#), "{hits}");
    assert!(hits.contains("added.rs"), "{hits}");

    // A later edit is seen on the next call.
    std::fs::write(
        trees.tree.join("src/added.rs"),
        "pub fn added_then_renamed() -> u32 {\n    8\n}\n",
    )
    .unwrap();
    let again = server.call(
        "find_definition",
        &format!(r#"{{"symbol":"added_then_renamed","worktree_root":"{wt}"}}"#),
    );
    assert!(again.contains(r#""count":1"#), "{again}");

    // The main index still answers for main, and its files are unchanged.
    let main_view = server.call("find_definition", r#"{"symbol":"added_in_worktree"}"#);
    assert!(main_view.contains(r#""count":0"#), "{main_view}");
    assert_eq!(std::fs::read(&snapshot).unwrap(), before);

    // A bad path is a one-line error that says what to do.
    let missing = json_path(&trees.root.join("missing"));
    let err = server.call(
        "find_definition",
        &format!(r#"{{"symbol":"x","worktree_root":"{missing}"}}"#),
    );
    assert!(err.contains("does not exist"), "{err}");
    let other = trees.root.join("other");
    std::fs::create_dir_all(other.join(".git")).unwrap();
    let err = server.call(
        "find_definition",
        &format!(
            r#"{{"symbol":"x","worktree_root":"{}"}}"#,
            json_path(&other)
        ),
    );
    assert!(err.contains("is not a checkout of this project"), "{err}");
}

/// A server started inside a worktree with no index of its own answers the
/// read tools for that worktree from the main checkout's index.
#[test]
fn a_server_in_an_unindexed_worktree_uses_the_main_index() {
    let trees = Trees::new("auto");
    std::fs::write(
        trees.tree.join("src/added.rs"),
        "pub fn only_in_the_worktree() {}\n",
    )
    .unwrap();
    let mut server = Server::start(&trees.tree);
    let found = server.call("find_definition", r#"{"symbol":"only_in_the_worktree"}"#);
    assert!(found.contains(r#""count":1"#), "{found}");
    assert!(found.contains(r#""overlay_files":1"#), "{found}");
    let base = server.call("find_definition", r#"{"symbol":"retired_helper"}"#);
    assert!(base.contains(r#""count":1"#), "{base}");
    assert!(!trees
        .tree
        .join(".stele-context/store/snapshot.json")
        .exists());
}
