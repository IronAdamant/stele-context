//! Rust references that 1.5.2's line patterns miss (2.3).
//!
//! 1.5.2 records a Rust reference only for `impl X` and for the last name of
//! a simple `use a::b::X;`. A grouped import (`use a::{X, Y}`) gives no
//! name, and a qualified use in code (`crate::a::X`, `a::f(...)`) or a type
//! in a signature (`&mut dyn X`) gives nothing. This adds, once per name and
//! chunk at its first line:
//! - every name a `use` tree brings in, grouped and nested included
//!   (`X` of `X as Y`);
//! - the item a `module::item` path names, and every capitalised segment
//!   of a path (`a::Type::new` gives `Type`);
//! - capitalised type names in code (`dyn Trait`, `impl Trait for Type`,
//!   `Vec<Type>`), except on the line that defines them.
//! Comments, string and char literals are skipped.

use super::Hit;
use std::collections::HashSet;

/// Roots, keywords, and the standard prelude and std types: a reference
/// to `Vec` or `Some` would link every chunk to nothing in the project.
const SKIP: &[&str] = &[
    "crate",
    "self",
    "super",
    "Self",
    "use",
    "as",
    "Some",
    "None",
    "Ok",
    "Err",
    "Vec",
    "String",
    "Option",
    "Result",
    "Box",
    "HashMap",
    "HashSet",
    "BTreeMap",
    "BTreeSet",
    "VecDeque",
    "BinaryHeap",
    "Rc",
    "Arc",
    "Weak",
    "RefCell",
    "Cell",
    "OnceCell",
    "OnceLock",
    "Mutex",
    "RwLock",
    "Path",
    "PathBuf",
    "Default",
    "Debug",
    "Clone",
    "Copy",
    "PartialEq",
    "Eq",
    "Hash",
    "Hasher",
    "Ord",
    "PartialOrd",
    "Ordering",
    "Display",
    "Formatter",
    "Iterator",
    "IntoIterator",
    "DoubleEndedIterator",
    "ExactSizeIterator",
    "Extend",
    "From",
    "Into",
    "TryFrom",
    "TryInto",
    "Fn",
    "FnMut",
    "FnOnce",
    "Send",
    "Sync",
    "Sized",
    "Unpin",
    "ToString",
    "ToOwned",
    "AsRef",
    "AsMut",
    "Borrow",
    "BorrowMut",
    "Cow",
    "Deref",
    "DerefMut",
    "Drop",
    "Error",
    "Duration",
    "Instant",
    "SystemTime",
    "PhantomData",
    "Read",
    "Write",
    "BufRead",
    "BufReader",
    "BufWriter",
    "File",
    "Any",
    "TypeId",
    "Range",
    "Infallible",
];

pub(super) fn extra(content: &str, base: &[Hit]) -> Vec<Hit> {
    let code = strip(content);
    let tokens = tokenize(&code);
    let mut seen: HashSet<String> = base
        .iter()
        .filter(|hit| hit.role == "reference")
        .map(|hit| hit.name.clone())
        .collect();
    let defined: HashSet<(String, u32)> = base
        .iter()
        .filter(|hit| hit.role == "definition")
        .map(|hit| (hit.name.clone(), hit.line))
        .collect();
    let mut out = Vec::new();
    let mut emit = |name: &str, kind: &str, line: u32, out: &mut Vec<Hit>| {
        if SKIP.contains(&name) || name == "_" || !seen.insert(name.to_string()) {
            return;
        }
        out.push(Hit {
            name: name.to_string(),
            kind: kind.to_string(),
            role: "reference".to_string(),
            line,
            container: None,
        });
    };
    let mut index = 0usize;
    while index < tokens.len() {
        let token = &tokens[index];
        if token.text == "use" && token.ident {
            index = use_tree(&tokens, index + 1, &mut |name, line| {
                emit(name, "import", line, &mut out)
            });
            continue;
        }
        if token.ident && tokens.get(index + 1).is_some_and(|next| next.text == "::") {
            // A path: walk its segments.
            let mut segments: Vec<&Token> = vec![token];
            let mut at = index + 1;
            while at + 1 < tokens.len() && tokens[at].text == "::" && tokens[at + 1].ident {
                segments.push(&tokens[at + 1]);
                at += 2;
            }
            // `module::item` names the item; `Type::item` is an associated
            // item (`Vec::new`, `Json::get`), which would match every `new`
            // in the project, so only the type counts there.
            let last = segments.len() - 1;
            for (position, segment) in segments.iter().enumerate() {
                let item_of_module =
                    position == last && position > 0 && !capitalised(&segments[position - 1].text);
                if item_of_module || capitalised(&segment.text) {
                    emit(&segment.text, "path", segment.line, &mut out);
                }
            }
            index = at;
            continue;
        }
        if token.ident
            && capitalised(&token.text)
            && !defined.contains(&(token.text.clone(), token.line))
            && !index
                .checked_sub(1)
                .is_some_and(|before| tokens[before].text == ".")
        {
            emit(&token.text, "type", token.line, &mut out);
        }
        index += 1;
    }
    out
}

/// `Name` or `NameTwo`, not `NAME` or `name`.
fn capitalised(name: &str) -> bool {
    name.chars()
        .next()
        .is_some_and(|ch| ch.is_ascii_uppercase())
        && name.chars().any(|ch| ch.is_ascii_lowercase())
}

/// The names a `use` tree imports, from `start` (just after `use`) to its
/// `;`. Returns the index after the `;`.
fn use_tree(tokens: &[Token], start: usize, found: &mut dyn FnMut(&str, u32)) -> usize {
    let mut index = start;
    let mut depth = 0i32;
    while index < tokens.len() {
        let token = &tokens[index];
        match token.text.as_str() {
            ";" if depth <= 0 => return index + 1,
            "{" => depth += 1,
            "}" => {
                depth -= 1;
                if depth < 0 {
                    return index + 1;
                }
            }
            _ if token.ident => {
                // A leaf is a name not followed by `::`. In `X as Y` the
                // import is X; Y is a local alias.
                let next = tokens.get(index + 1).map(|next| next.text.as_str());
                let alias = index
                    .checked_sub(1)
                    .is_some_and(|before| tokens[before].text == "as");
                if next != Some("::") && !alias {
                    found(&token.text, token.line);
                }
            }
            _ => {}
        }
        index += 1;
    }
    index
}

struct Token {
    text: String,
    ident: bool,
    line: u32,
}

/// Identifiers, `::`, and single punctuation, with 1-based lines.
fn tokenize(code: &[char]) -> Vec<Token> {
    let mut tokens = Vec::new();
    let mut line = 1u32;
    let mut index = 0usize;
    while index < code.len() {
        let ch = code[index];
        if ch == '\n' {
            line += 1;
            index += 1;
            continue;
        }
        if ch.is_whitespace() {
            index += 1;
            continue;
        }
        if ch.is_alphabetic() || ch == '_' {
            let start = index;
            while index < code.len() && (code[index].is_alphanumeric() || code[index] == '_') {
                index += 1;
            }
            // `r#name` raw identifiers.
            let text: String = code[start..index].iter().collect();
            if text == "r" && code.get(index) == Some(&'#') {
                index += 1;
                continue;
            }
            tokens.push(Token {
                text,
                ident: true,
                line,
            });
            continue;
        }
        if ch.is_ascii_digit() {
            while index < code.len() && (code[index].is_alphanumeric() || code[index] == '_') {
                index += 1;
            }
            continue;
        }
        if ch == ':' && code.get(index + 1) == Some(&':') {
            tokens.push(Token {
                text: "::".to_string(),
                ident: false,
                line,
            });
            index += 2;
            continue;
        }
        tokens.push(Token {
            text: ch.to_string(),
            ident: false,
            line,
        });
        index += 1;
    }
    tokens
}

/// The chunk with comments and string and char literals blanked (newlines
/// kept, so line numbers hold).
fn strip(content: &str) -> Vec<char> {
    let chars: Vec<char> = content.chars().collect();
    let mut out = chars.clone();
    let blank = |out: &mut Vec<char>, from: usize, to: usize| {
        for slot in out.iter_mut().take(to).skip(from) {
            if *slot != '\n' {
                *slot = ' ';
            }
        }
    };
    let mut index = 0usize;
    while index < chars.len() {
        let ch = chars[index];
        let next = chars.get(index + 1).copied();
        if ch == '/' && next == Some('/') {
            let end = chars[index..]
                .iter()
                .position(|item| *item == '\n')
                .map(|at| index + at)
                .unwrap_or(chars.len());
            blank(&mut out, index, end);
            index = end;
            continue;
        }
        if ch == '/' && next == Some('*') {
            let mut depth = 0i32;
            let mut at = index;
            while at < chars.len() {
                if chars[at] == '/' && chars.get(at + 1) == Some(&'*') {
                    depth += 1;
                    at += 2;
                } else if chars[at] == '*' && chars.get(at + 1) == Some(&'/') {
                    depth -= 1;
                    at += 2;
                    if depth == 0 {
                        break;
                    }
                } else {
                    at += 1;
                }
            }
            blank(&mut out, index, at.min(chars.len()));
            index = at;
            continue;
        }
        // Raw strings: r"..", r#".."#, br#".."#.
        if (ch == 'r' || (ch == 'b' && next == Some('r')))
            && !index
                .checked_sub(1)
                .is_some_and(|before| chars[before].is_alphanumeric() || chars[before] == '_')
        {
            let mut at = index + if ch == 'b' { 2 } else { 1 };
            let mut hashes = 0usize;
            while chars.get(at) == Some(&'#') {
                hashes += 1;
                at += 1;
            }
            if chars.get(at) == Some(&'"') {
                let body = at + 1;
                let mut end = body;
                loop {
                    if end >= chars.len() {
                        break;
                    }
                    if chars[end] == '"'
                        && (0..hashes).all(|offset| chars.get(end + 1 + offset) == Some(&'#'))
                    {
                        end += 1 + hashes;
                        break;
                    }
                    end += 1;
                }
                blank(&mut out, index, end.min(chars.len()));
                index = end;
                continue;
            }
        }
        if ch == '"' {
            let mut at = index + 1;
            while at < chars.len() && chars[at] != '"' {
                if chars[at] == '\\' {
                    at += 1;
                }
                at += 1;
            }
            let end = (at + 1).min(chars.len());
            blank(&mut out, index, end);
            index = end;
            continue;
        }
        if ch == '\'' {
            // A char literal ('a', '\n', '\u{..}'), not a lifetime ('a).
            let close = if next == Some('\\') {
                chars[index + 2..]
                    .iter()
                    .take(12)
                    .position(|item| *item == '\'')
                    .map(|at| index + 2 + at)
            } else if chars.get(index + 2) == Some(&'\'') {
                Some(index + 2)
            } else {
                None
            };
            if let Some(close) = close {
                blank(&mut out, index, close + 1);
                index = close + 1;
                continue;
            }
        }
        index += 1;
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn names(content: &str) -> Vec<(String, String, u32)> {
        let base = super::super::extract("rs", content);
        let mut rows: Vec<(String, String, u32)> = extra(content, &base)
            .into_iter()
            .map(|hit| (hit.name, hit.kind, hit.line))
            .collect();
        rows.sort();
        rows
    }

    #[test]
    fn grouped_imports_paths_and_types() {
        let content = "use crate::write_policy::{WorldWrite, self, Exempt as Ex, inner::{Rule, deep}};\n\
                       use specula_core::checksum::crc32;\n\
                       // WorldError in a comment, \"Opener\" in text\n\
                       pub fn over(world: &mut dyn crate::write_policy::Writer) -> Box<dyn Shape> {\n\
                           let x = 'a'; let y: &'static str = \"Mark\";\n\
                           specula_core::checksum::crc32_update(x, y)\n\
                       }\n\
                       pub struct Defined;\n";
        let rows = names(content);
        let got: Vec<&str> = rows.iter().map(|(name, _, _)| name.as_str()).collect();
        for want in [
            "WorldWrite",
            "Exempt",
            "Rule",
            "deep",
            "Writer",
            "Shape",
            "crc32_update",
        ] {
            assert!(got.contains(&want), "{want}: {rows:?}");
        }
        for not in [
            "self",
            "Ex",
            "WorldError",
            "Opener",
            "Mark",
            "inner",
            "write_policy",
            "Defined",
            "crc32",
            "Box",
        ] {
            assert!(!got.contains(&not), "{not}: {rows:?}");
        }
        assert!(
            rows.contains(&("Writer".to_string(), "path".to_string(), 4)),
            "{rows:?}"
        );
        assert!(
            rows.contains(&("Rule".to_string(), "import".to_string(), 1)),
            "{rows:?}"
        );
    }

    #[test]
    fn single_segment_paths_do_not_panic() {
        let rows = names(
            "let v = Vec::<u8>::new();
use a::*;
foo::{x};
",
        );
        assert!(rows.iter().all(|(name, _, _)| name != "new"), "{rows:?}");
    }

    #[test]
    fn multi_line_use_and_raw_strings() {
        let content = "use a::{\n    B,\n    c::D,\n};\nconst S: &str = r#\"Fake \"quote\"\"#;\nfn f(_: Elem) {}\n";
        let rows = names(content);
        let got: Vec<&str> = rows.iter().map(|(name, _, _)| name.as_str()).collect();
        assert!(
            got.contains(&"B") && got.contains(&"D") && got.contains(&"Elem"),
            "{rows:?}"
        );
        assert!(!got.contains(&"Fake"), "{rows:?}");
        assert!(
            rows.contains(&("D".to_string(), "import".to_string(), 3)),
            "{rows:?}"
        );
    }
}
