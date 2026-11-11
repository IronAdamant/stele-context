use super::*;
use crate::engine::Engine;

/// A main checkout and a linked worktree of it, laid out the way git does
/// (`.git` directory; the worktree's `.git` file points to
/// `.git/worktrees/wt`, whose `commondir` leads back), without running git.
struct Trees {
    root: PathBuf,
    main: PathBuf,
    tree: PathBuf,
}

impl Trees {
    fn new(tag: &str) -> Trees {
        let root = std::env::temp_dir().join(format!(
            "stele-overlay-{tag}-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_nanos())
                .unwrap_or(0)
        ));
        let main = root.join("main");
        let tree = root.join("wt");
        fs::create_dir_all(main.join(".git").join("worktrees").join("wt")).unwrap();
        fs::write(main.join(".git").join("HEAD"), "ref: refs/heads/main\n").unwrap();
        fs::write(
            main.join(".git")
                .join("worktrees")
                .join("wt")
                .join("commondir"),
            "../..\n",
        )
        .unwrap();
        fs::create_dir_all(main.join("src")).unwrap();
        fs::write(
            main.join("src/a.rs"),
            "pub fn alpha() -> u32 {\n    beta()\n}\n",
        )
        .unwrap();
        fs::write(main.join("src/b.rs"), "pub fn beta() -> u32 {\n    1\n}\n").unwrap();
        fs::write(main.join("src/c.rs"), "pub fn gamma() {}\n").unwrap();
        fs::create_dir_all(tree.join("src")).unwrap();
        for name in ["a.rs", "b.rs", "c.rs"] {
            fs::copy(main.join("src").join(name), tree.join("src").join(name)).unwrap();
        }
        let gitdir = main.join(".git").join("worktrees").join("wt");
        fs::write(tree.join(".git"), format!("gitdir: {}\n", gitdir.display())).unwrap();
        Trees { root, main, tree }
    }

    fn base(&self) -> Engine {
        let mut engine =
            Engine::open_at(self.main.clone(), self.main.join(".stele-context")).unwrap();
        engine.index(&[], false).unwrap();
        engine
    }

    fn tree_text(&self) -> String {
        self.tree.display().to_string()
    }

    fn snapshot(&self) -> Vec<u8> {
        fs::read(
            self.main
                .join(".stele-context")
                .join("store")
                .join("snapshot.json"),
        )
        .unwrap()
    }
}

impl Drop for Trees {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.root);
    }
}

fn paths(value: &Value, key: &str) -> Vec<String> {
    match field(value, key) {
        Some(Value::Array(rows)) => rows
            .iter()
            .map(|row| crate::store::string_field(row, "document_path").replace('\\', "/"))
            .collect(),
        _ => Vec::new(),
    }
}

fn count(value: &Value) -> i64 {
    crate::store::int_field(value, "count")
}

#[test]
fn edited_added_and_deleted_files_answer_from_the_worktree() {
    let trees = Trees::new("edit");
    let mut base = trees.base();
    let before = trees.snapshot();
    // Edit one file, add one, delete one.
    fs::write(
        trees.tree.join("src/a.rs"),
        "pub fn alpha_two() -> u32 {\n    beta() + beta()\n}\n",
    )
    .unwrap();
    fs::write(
        trees.tree.join("src/d.rs"),
        "macro_rules! delta_rule { () => {} }\npub fn delta() {\n    delta_rule!();\n}\n",
    )
    .unwrap();
    fs::remove_file(trees.tree.join("src/c.rs")).unwrap();

    let mut overlays = Overlays::new();
    let tree = trees.tree_text();
    let (found, ran) = overlays
        .run(&mut base, &tree, |engine| engine.find_definition("delta"))
        .unwrap();
    assert_eq!(count(&found), 1, "{}", json::to_string(&found));
    assert_eq!(paths(&found, "definitions"), vec!["src/d.rs".to_string()]);
    assert_eq!(ran.overlay_files, 3);
    assert!(same_path(Path::new(&ran.worktree), &trees.tree));
    let (gone, _) = overlays
        .run(&mut base, &tree, |engine| engine.find_definition("gamma"))
        .unwrap();
    assert_eq!(count(&gone), 0);
    let (old, _) = overlays
        .run(&mut base, &tree, |engine| engine.find_definition("alpha"))
        .unwrap();
    assert_eq!(count(&old), 0);
    let (new, _) = overlays
        .run(&mut base, &tree, |engine| {
            engine.find_definition("alpha_two")
        })
        .unwrap();
    assert_eq!(count(&new), 1);
    // A macro defined and invoked in the new file.
    let (refs, _) = overlays
        .run(&mut base, &tree, |engine| {
            engine.find_references_in("delta_rule", false)
        })
        .unwrap();
    assert_eq!(
        crate::store::string_field(&refs, "verdict"),
        "referenced",
        "{}",
        json::to_string(&refs)
    );
    // Keyword search sees the new file's text.
    let (hits, _) = overlays
        .run(&mut base, &tree, |engine| {
            engine.search("delta", "keyword", 5)
        })
        .unwrap();
    let Value::Array(rows) = &hits else {
        panic!("{}", json::to_string(&hits));
    };
    assert!(rows
        .iter()
        .any(|row| crate::store::string_field(row, "document_path").ends_with("d.rs")));

    // The base index answers for the main checkout and is unchanged on disk.
    assert_eq!(count(&base.find_definition("delta")), 0);
    assert_eq!(count(&base.find_definition("gamma")), 1);
    assert_eq!(trees.snapshot(), before);

    // Another edit is seen on the next call (size changes, so the stamp
    // check catches it); reverting a file to main's text drops it.
    fs::write(trees.tree.join("src/d.rs"), "pub fn epsilon_renamed() {}\n").unwrap();
    fs::copy(trees.main.join("src/a.rs"), trees.tree.join("src/a.rs")).unwrap();
    let (renamed, ran) = overlays
        .run(&mut base, &tree, |engine| {
            engine.find_definition("epsilon_renamed")
        })
        .unwrap();
    assert_eq!(count(&renamed), 1);
    assert_eq!(ran.overlay_files, 2, "d.rs new, c.rs deleted");
    let (back, _) = overlays
        .run(&mut base, &tree, |engine| engine.find_definition("alpha"))
        .unwrap();
    assert_eq!(count(&back), 1);

    // The stamps are kept for the next process, which answers the same.
    let manifest = base
        .storage
        .join("overlays")
        .join(key_of(&trees.tree))
        .join("overlay.json");
    let text = fs::read_to_string(&manifest).unwrap();
    assert!(text.contains("\"worktree\""), "{text}");
    let mut fresh = Overlays::new();
    let (again, ran) = fresh
        .run(&mut base, &tree, |engine| {
            engine.find_definition("epsilon_renamed")
        })
        .unwrap();
    assert_eq!(count(&again), 1);
    assert_eq!(ran.overlay_files, 2);
    assert_eq!(trees.snapshot(), before);
}

#[test]
fn the_base_checkout_itself_runs_on_the_base_index() {
    let trees = Trees::new("self");
    let mut base = trees.base();
    let main = trees.main.display().to_string();
    let (found, ran) = Overlays::new()
        .run(&mut base, &main, |engine| engine.find_definition("gamma"))
        .unwrap();
    assert_eq!(count(&found), 1);
    assert_eq!(ran.overlay_files, 0);
    assert!(!base.storage.join("overlays").exists());
}

#[test]
fn bad_worktree_roots_get_one_line_errors() {
    let trees = Trees::new("bad");
    let mut base = trees.base();
    let mut overlays = Overlays::new();
    let missing = trees.root.join("nope").display().to_string();
    let err = overlays.run(&mut base, &missing, |_| ()).err().unwrap();
    assert!(err.contains("does not exist"), "{err}");
    assert!(!err.contains('\n'), "{err}");
    let err = overlays
        .run(&mut base, "relative/path", |_| ())
        .err()
        .unwrap();
    assert!(err.contains("absolute path"), "{err}");
    // A different git project.
    let other = trees.root.join("other");
    fs::create_dir_all(other.join(".git")).unwrap();
    let err = overlays
        .run(&mut base, &other.display().to_string(), |_| ())
        .err()
        .unwrap();
    assert!(err.contains("is not a checkout of this project"), "{err}");
    assert!(err.contains("worktree list"), "{err}");
}

#[test]
fn overlays_of_removed_worktrees_are_pruned() {
    let trees = Trees::new("prune");
    let mut base = trees.base();
    let stale = base.storage.join("overlays").join("0123456789abcdef");
    fs::create_dir_all(&stale).unwrap();
    fs::write(
        stale.join("overlay.json"),
        json::to_string(&Value::object(vec![(
            "worktree",
            Value::string(trees.root.join("removed").display().to_string()),
        )])),
    )
    .unwrap();
    let tree = trees.tree_text();
    Overlays::new()
        .run(&mut base, &tree, |engine| engine.find_definition("beta"))
        .unwrap();
    assert!(!stale.exists());
    assert!(base
        .storage
        .join("overlays")
        .join(key_of(&trees.tree))
        .join("overlay.json")
        .is_file());
}

#[test]
fn replies_carry_the_worktree_and_overlay_count() {
    let ran = Ran {
        worktree: "/w".to_string(),
        overlay_files: 2,
    };
    let object = annotate(Value::object(vec![("count", Value::Int(1))]), &ran);
    assert_eq!(
        json::to_string(&object),
        r#"{"count":1,"worktree":"/w","overlay_files":2}"#
    );
    let array = annotate(Value::Array(vec![Value::Int(1)]), &ran);
    assert_eq!(
        json::to_string(&array),
        r#"{"results":[1],"worktree":"/w","overlay_files":2}"#
    );
}

#[test]
fn a_server_inside_an_unindexed_worktree_answers_for_it() {
    let trees = Trees::new("auto");
    let _base = trees.base();
    let (main, tree) = auto_worktree(&trees.tree.join("src"), None).unwrap();
    assert!(same_path(&main, &trees.main.join("src")));
    assert!(same_path(&tree, &trees.tree.join("src")));
    // The main checkout itself, a storage flag, or a worktree with its own
    // index: unchanged.
    assert!(auto_worktree(&trees.main, None).is_none());
    assert!(auto_worktree(&trees.tree, Some(Path::new("x"))).is_none());
    fs::create_dir_all(trees.tree.join(".stele-context").join("store")).unwrap();
    fs::write(
        trees
            .tree
            .join(".stele-context")
            .join("store")
            .join("snapshot.json"),
        "{}",
    )
    .unwrap();
    assert!(auto_worktree(&trees.tree, None).is_none());
}
