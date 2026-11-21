//! The lines of a symbol's own definition (A/B run, 2026-11-21). A
//! reference inside them (a recursive call, a class built in its own
//! method, `Vec<Tree>` inside `struct Tree`) is not a use from outside, so
//! `find_references` lists it under `self_references` and the dead-code
//! verdict does not count it.
//!
//! - Python: the reference's scope (`container`) is the definition's
//!   qualified name or lies below it. Exact, from the parser.
//! - Brace languages (Rust, JavaScript/TypeScript, JVM, Go, C, C#, Swift,
//!   PHP): from the definition line, the header runs to the first `{` at
//!   bracket depth 0 and the body to its matching `}`; strings and comments
//!   are skipped. A `;` before the `{` ends a declaration with no body (the
//!   span is the definition line). A header line after the first must be
//!   inside open brackets, indented deeper, or start with `{`, `where`,
//!   `)`, `->`, `:`, `extends`, `implements` or `throws`; any other line
//!   ends the search, so a body-less arrow function does not take the next
//!   function's braces.
//! - Anything else, or a span that does not close: no own body (every row
//!   stays a reference).

use super::query::language_of;
use super::Engine;
use crate::store::Chunk;

/// Where a definition's own lines are.
pub(super) enum OwnBody {
    /// Python: references whose scope is `qualified` or lies below it.
    Scope { path: String, qualified: String },
    /// Brace languages: the 1-based lines `first..=last` of `path`.
    Lines { path: String, first: i64, last: i64 },
}

impl OwnBody {
    /// The reference `(path, line)` with Python scope `container` is inside.
    pub(super) fn holds(&self, path: &str, line: i64, container: Option<&str>) -> bool {
        match self {
            OwnBody::Scope {
                path: own,
                qualified,
            } => {
                own == path
                    && container.is_some_and(|scope| {
                        scope == qualified
                            || scope
                                .strip_prefix(qualified.as_str())
                                .is_some_and(|rest| rest.starts_with('.'))
                    })
            }
            OwnBody::Lines {
                path: own,
                first,
                last,
            } => own == path && *first <= line && line <= *last,
        }
    }
}

const BRACE_LANGUAGES: &[&str] = &[
    "rust",
    "javascript",
    "jvm",
    "go",
    "c",
    "csharp",
    "swift",
    "php",
];

/// Lines a header may span before its `{`.
const HEADER_LINES: usize = 30;

impl Engine {
    /// The own body of the definition `name` at `path:line`, when it can be
    /// told.
    pub(super) fn own_body(&self, name: &str, path: &str, line: i64) -> Option<OwnBody> {
        let language = language_of(path);
        if language == "python" {
            let def = self.store.state.symbols.iter().find(|sym| {
                sym.name == name
                    && sym.role == "definition"
                    && sym.path == path
                    && sym.line as i64 == line
            })?;
            let qualified = match &def.container {
                Some(scope) if !scope.is_empty() => format!("{scope}.{name}"),
                _ => name.to_string(),
            };
            return Some(OwnBody::Scope {
                path: path.to_string(),
                qualified,
            });
        }
        if !BRACE_LANGUAGES.contains(&language) || line < 1 {
            return None;
        }
        let mut chunks: Vec<&Chunk> = self
            .store
            .state
            .chunks
            .iter()
            .filter(|chunk| chunk.path == path)
            .collect();
        if chunks.is_empty() || (chunks.len() > 1 && chunks.iter().any(|c| c.line_map.is_empty())) {
            return None;
        }
        chunks.sort_by_key(|chunk| chunk.start);
        let (text, _) = super::references::file_text(&chunks);
        let lines: Vec<&str> = text.split('\n').collect();
        let last = brace_span(&lines, line as usize, language == "rust")?;
        Some(OwnBody::Lines {
            path: path.to_string(),
            first: line,
            last: last as i64,
        })
    }

    /// The Python scope of the reference row at `path:line` named `name`.
    pub(super) fn reference_scope(&self, name: &str, path: &str, line: i64) -> Option<&str> {
        self.store
            .state
            .symbols
            .iter()
            .find(|sym| {
                sym.name == name
                    && sym.role == "reference"
                    && sym.path == path
                    && sym.line as i64 == line
            })
            .and_then(|sym| sym.container.as_deref())
    }
}

fn indent(line: &str) -> usize {
    line.len() - line.trim_start().len()
}

/// The last line (1-based) of the body that opens at or after `first`, or
/// `first` itself for a declaration with no body. None when it cannot be
/// told.
pub(super) fn brace_span(lines: &[&str], first: usize, rust: bool) -> Option<usize> {
    if first == 0 || first > lines.len() {
        return None;
    }
    let base_indent = indent(lines[first - 1]);
    let mut brackets = 0i64; // ( and [ depth
    let mut braces = 0i64;
    let mut opened = false;
    let mut block_comment = false;
    for (at, raw) in lines.iter().enumerate().skip(first - 1) {
        let number = at + 1;
        if !opened && number > first {
            if number - first > HEADER_LINES {
                return Some(first);
            }
            let trimmed = raw.trim_start();
            let continues = brackets > 0
                || block_comment
                || trimmed.is_empty()
                || indent(raw) > base_indent
                || [
                    "{",
                    "where",
                    ")",
                    "]",
                    "->",
                    ":",
                    "extends",
                    "implements",
                    "throws",
                ]
                .iter()
                .any(|lead| trimmed.starts_with(lead));
            if !continues {
                return Some(first);
            }
        }
        let chars: Vec<char> = raw.chars().collect();
        let mut index = 0usize;
        while index < chars.len() {
            let ch = chars[index];
            let next = chars.get(index + 1).copied();
            if block_comment {
                if ch == '*' && next == Some('/') {
                    block_comment = false;
                    index += 2;
                } else {
                    index += 1;
                }
                continue;
            }
            match ch {
                '/' if next == Some('/') => break,
                '/' if next == Some('*') => {
                    block_comment = true;
                    index += 2;
                    continue;
                }
                '"' | '`' => {
                    index += 1;
                    while index < chars.len() && chars[index] != ch {
                        if chars[index] == '\\' {
                            index += 1;
                        }
                        index += 1;
                    }
                }
                '\'' => {
                    // A char literal ('x', '\n'); in Rust a lone ' is a
                    // lifetime.
                    if next == Some('\\') {
                        index += 2;
                        while index < chars.len() && chars[index] != '\'' {
                            index += 1;
                        }
                    } else if chars.get(index + 2) == Some(&'\'') {
                        index += 2;
                    } else if !rust {
                        index += 1;
                        while index < chars.len() && chars[index] != '\'' {
                            if chars[index] == '\\' {
                                index += 1;
                            }
                            index += 1;
                        }
                    }
                }
                '(' | '[' => brackets += 1,
                ')' | ']' => brackets -= 1,
                ';' if !opened && brackets <= 0 => return Some(first),
                '{' => {
                    if opened || brackets <= 0 {
                        opened = true;
                        braces += 1;
                    }
                }
                '}' if opened => {
                    braces -= 1;
                    if braces == 0 {
                        return Some(number);
                    }
                }
                _ => {}
            }
            index += 1;
        }
    }
    None
}

#[cfg(test)]
mod tests {
    use super::brace_span;

    fn span(text: &str, first: usize) -> Option<usize> {
        let lines: Vec<&str> = text.split('\n').collect();
        brace_span(&lines, first, true)
    }

    #[test]
    fn bodies_end_at_the_matching_brace() {
        let text = "pub fn walk(n: u32) -> u32 {\n    if n == 0 { 0 } else { walk(n - 1) }\n}\n\npub fn other() {}\n";
        assert_eq!(span(text, 1), Some(3));
        assert_eq!(span(text, 5), Some(5));
        // Braces in strings, chars and comments do not count; lifetimes are
        // not char literals.
        let text = "fn f<'a>(x: &'a str) {\n    let s = \"}\"; // }\n    let c = '}';\n    /* } */\n}\nfn g() {}\n";
        assert_eq!(span(text, 1), Some(5));
    }

    #[test]
    fn where_clauses_continue_the_header_and_declarations_have_no_body() {
        let text = "fn f<T>(x: T) -> u32\nwhere\n    T: Copy,\n{\n    f(x)\n}\n";
        assert_eq!(span(text, 1), Some(6));
        assert_eq!(
            span("pub struct Id(u32, Box<Id>);\nfn g() {}\n", 1),
            Some(1)
        );
        assert_eq!(span("trait T {\n    fn m(&self);\n}\n", 2), Some(2));
    }

    #[test]
    fn a_body_less_line_does_not_take_the_next_function() {
        let lines: Vec<&str> = "const f = (x) => x + 1\nfunction g() { f(2) }\n"
            .split('\n')
            .collect();
        assert_eq!(brace_span(&lines, 1, false), Some(1));
        // Unclosed: no span.
        let lines: Vec<&str> = "fn f() {\n    loop {\n".split('\n').collect();
        assert_eq!(brace_span(&lines, 1, true), None);
    }

    /// A/B run, 2026-11-21: uses inside a symbol's own body made a dead
    /// symbol look used. They are listed apart and not counted.
    #[test]
    fn own_body_uses_do_not_count_as_references() {
        let root = crate::test_dir("own-body");
        std::fs::create_dir_all(root.join("p")).unwrap();
        std::fs::write(
            root.join("p/m.py"),
            "def fact(n):\n    return 1 if n <= 1 else n * fact(n - 1)\n\n\nclass Node:\n    def clone(self):\n        return Node()\n\n\ndef used(n):\n    return used(n - 1) if n else 0\n",
        )
        .unwrap();
        std::fs::write(
            root.join("p/u.py"),
            "from p.m import used\nprint(used(3))\n",
        )
        .unwrap();
        std::fs::write(
            root.join("p/r.rs"),
            "pub fn walk(n: u32) -> u32 {\n    if n == 0 { 0 } else { walk(n - 1) }\n}\n\npub struct Tree {\n    kids: Vec<Tree>,\n}\n\npub fn run() -> u32 {\n    walk(2)\n}\n",
        )
        .unwrap();
        let mut engine =
            crate::engine::Engine::open_at(root.clone(), root.join(".stele-context")).unwrap();
        engine.index(&[], false).unwrap();
        let check = |name: &str, verdict: &str, total: i64, own: usize| {
            let found = engine.find_references(name);
            let text = crate::json::to_string(&found);
            assert_eq!(
                crate::store::string_field(&found, "verdict"),
                verdict,
                "{text}"
            );
            assert_eq!(crate::store::int_field(&found, "total"), total, "{text}");
            assert_eq!(
                text.matches("\"line_number\"").count(),
                1 + total as usize + own,
                "{text}"
            );
        };
        // Recursion only, a class built only in its own method, a struct
        // named only in its own field: unreferenced.
        check("fact", "unreferenced", 0, 1);
        check("Node", "unreferenced", 0, 1);
        check("Tree", "unreferenced", 0, 1);
        // Recursive and called from outside: referenced, the outside uses
        // counted, the definition not.
        check("used", "referenced", 2, 1);
        check("walk", "referenced", 1, 1);
        let fact = crate::json::to_string(&engine.find_references("fact"));
        assert!(fact.contains("\"self_references\""), "{fact}");
        assert!(
            fact.contains("only inside its own definition (1 line:"),
            "{fact}"
        );
        let _ = std::fs::remove_dir_all(&root);
    }
}
