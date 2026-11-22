//! 2.6, from an A/B run renaming a class across sqlglot: `find_references
//! StrPosition` listed only the calls (`exp.StrPosition(..)`), not the dict
//! keys, annotations and attribute chains grep finds, and `named_in` had
//! nothing for the dispatch names built from the class name
//! (`def strposition_sql`, `"STR_POSITION"`).

use std::path::PathBuf;
use std::process::{Command, Stdio};

const BINARY: &str = env!("CARGO_BIN_EXE_stele-context");

const EXPRESSIONS: &str = "from pkg import exp\n\n\nclass Func:\n    pass\n\n\nclass StrPosition(Func):\n    arg_types = {\"this\": True}\n\n    def again(self):\n        return exp.StrPosition.from_arg_list\n";

const GENERATOR: &str = r#"from sqlglot import exp
from sqlglot.dialects.dialect import (
    rename_func,
    strposition_sql,
)


class Generator:
    TRANSFORMS = {
        exp.StrPosition: lambda self, e: strposition_sql(self, e),
    }

    def strposition_sql(self, expression: exp.StrPosition) -> str:
        return self.func("STR_POSITION", expression.this)
"#;

const PARSER: &str = r#"from sqlglot import exp

FUNCTIONS = {
    "LOCATE": exp.StrPosition.from_arg_list,
}
KINDS = (exp.Upper, exp.StrPosition)
"#;

struct Project {
    root: PathBuf,
}

impl Project {
    fn new() -> Project {
        let root = std::env::temp_dir().join(format!(
            "stele-pyattr-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_nanos())
                .unwrap_or(0)
        ));
        for dir in ["pkg/generators", ".git"] {
            std::fs::create_dir_all(root.join(dir)).unwrap();
        }
        std::fs::write(root.join(".git").join("HEAD"), "ref: refs/heads/main\n").unwrap();
        std::fs::write(root.join("pkg/expressions.py"), EXPRESSIONS).unwrap();
        std::fs::write(root.join("pkg/generators/base.py"), GENERATOR).unwrap();
        std::fs::write(root.join("pkg/parser.py"), PARSER).unwrap();
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

fn place(path: &str, line: u32) -> String {
    format!(r#""document_path":"{path}","line_number":{line}"#)
}

#[test]
fn every_use_of_a_python_class_is_a_reference() {
    let project = Project::new();
    let refs = project.cli(&["find-references", "StrPosition"]);
    assert!(refs.contains(r#""verdict":"referenced""#), "{refs}");
    for (path, line) in [
        // A dict key, an annotation, an attribute chain, a tuple element.
        ("pkg/generators/base.py", 10),
        ("pkg/generators/base.py", 13),
        ("pkg/parser.py", 4),
        ("pkg/parser.py", 6),
    ] {
        assert!(refs.contains(&place(path, line)), "{path}:{line}: {refs}");
    }
    assert!(refs.contains(r#""total":4"#), "{refs}");
    // A use inside the class's own body is a self reference, by the scope
    // the query-time read gives it.
    let own = refs.find(r#""self_references":"#).expect(&refs);
    assert!(
        refs[own..].contains(&place("pkg/expressions.py", 12)),
        "{refs}"
    );
    // The string "STR_POSITION" is not a code use of the name.
    assert!(
        !refs.contains(&place("pkg/generators/base.py", 14)),
        "{refs}"
    );
}

#[test]
fn named_in_lists_the_dispatch_spellings_of_a_camel_case_name() {
    let project = Project::new();
    let refs = project.cli(&["find-references", "StrPosition"]);
    let at = refs.find(r#""variants":"#).expect(&refs);
    let variants = &refs[at..];
    assert!(
        variants.contains(r#""forms":["strposition","str_position","STR_POSITION"]"#),
        "{variants}"
    );
    // The method a `f"{key}_sql"` dispatch reaches comes first, with the
    // name it defines; the import and the SQL name follow.
    assert!(
        variants.contains(
            r#"{"path":"pkg/generators/base.py","line":13,"variant":"strposition","defines":"strposition_sql"}"#
        ),
        "{variants}"
    );
    assert!(
        variants
            .contains(r#"{"path":"pkg/generators/base.py","line":14,"variant":"STR_POSITION"}"#),
        "{variants}"
    );
    assert!(
        variants.contains(r#"{"path":"pkg/generators/base.py","line":4,"variant":"strposition"}"#),
        "{variants}"
    );
    assert!(
        variants.contains(r#""by_variant":{"strposition":3,"STR_POSITION":1}"#),
        "{variants}"
    );
    // impact_radius carries the same block.
    let impact = project.cli(&["impact-radius", "StrPosition"]);
    assert!(
        impact.contains(r#""defines":"strposition_sql""#),
        "{impact}"
    );
    // A one-word name has no variants.
    let one = project.cli(&["find-references", "Generator"]);
    assert!(!one.contains(r#""variants":"#), "{one}");
}
