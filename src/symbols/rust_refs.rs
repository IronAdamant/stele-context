//! Rust symbols that 1.5.2's line patterns miss (2.3, 2.4).
//!
//! 1.5.2 records a Rust reference only for `impl X` and for the last name of
//! a simple `use a::b::X;`, and a definition only for `fn`, `struct`, `enum`
//! and `trait` at the start of a line (`pub` and `async` allowed). This
//! adds, once per name and chunk at its first line:
//! - every name a `use` tree brings in, grouped and nested included
//!   (`X` of `X as Y`);
//! - the item a `module::item` path names, and every capitalised segment
//!   of a path (`a::Type::new` gives `Type`);
//! - capitalised type names in code (`dyn Trait`, `impl Trait for Type`,
//!   `Vec<Type>`), except on the line that defines them;
//! - (2.4) calls: `name(..)` and `name::<T>(..)` (kind `call`), method and
//!   associated calls `.name(..)` and `Type::name(..)` (kind `method_call`),
//!   and macro invocations `name!(..)` (kind `macro`);
//! - (2.4) the definitions the line patterns miss: `pub(crate) fn`,
//!   `const fn`, `unsafe fn`, `extern "C" fn`, `pub(crate) struct`,
//!   `union`, `type` aliases, `mod`, `const` and `static` items, and
//!   `macro_rules! name` (kind `macro`).
//!
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

/// Keywords that look like a call (`if (..)`, `match (..)`) or that come
/// before a name without being one.
const KEYWORDS: &[&str] = &[
    "as", "async", "await", "box", "break", "const", "continue", "crate", "dyn", "else", "enum",
    "extern", "false", "fn", "for", "if", "impl", "in", "let", "loop", "match", "mod", "move",
    "mut", "pub", "ref", "return", "self", "Self", "static", "struct", "super", "trait", "true",
    "type", "union", "unsafe", "use", "where", "while", "yield",
];

/// Standard macros: an invocation names nothing in the project.
const STD_MACROS: &[&str] = &[
    "assert",
    "assert_eq",
    "assert_ne",
    "cfg",
    "column",
    "compile_error",
    "concat",
    "dbg",
    "debug_assert",
    "debug_assert_eq",
    "debug_assert_ne",
    "env",
    "eprint",
    "eprintln",
    "file",
    "format",
    "format_args",
    "include",
    "include_bytes",
    "include_str",
    "line",
    "macro_rules",
    "matches",
    "module_path",
    "option_env",
    "panic",
    "print",
    "println",
    "stringify",
    "thread_local",
    "todo",
    "unimplemented",
    "unreachable",
    "vec",
    "write",
    "writeln",
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
    let mut defined_now: HashSet<(String, u32)> = defined.clone();
    let define = |name: &str,
                  kind: &str,
                  line: u32,
                  out: &mut Vec<Hit>,
                  known: &mut HashSet<(String, u32)>| {
        if name == "_" || !known.insert((name.to_string(), line)) {
            return;
        }
        out.push(Hit {
            name: name.to_string(),
            kind: kind.to_string(),
            role: "definition".to_string(),
            line,
            container: None,
        });
    };
    let text = |at: usize| tokens.get(at).map(|token| token.text.as_str());
    let mut index = 0usize;
    while index < tokens.len() {
        let token = &tokens[index];
        if token.text == "use" && token.ident {
            index = use_tree(&tokens, index + 1, &mut |name, line| {
                emit(name, "import", line, &mut out)
            });
            continue;
        }
        if token.ident {
            if let Some((name, kind)) = definition_at(&tokens, index) {
                define(&name.text, kind, name.line, &mut out, &mut defined_now);
            }
        }
        if token.ident && token.text == "macro_rules" && text(index + 1) == Some("!") {
            if let Some(name) = tokens.get(index + 2).filter(|name| name.ident) {
                define(&name.text, "macro", name.line, &mut out, &mut defined_now);
                index += 3;
                continue;
            }
        }
        if token.ident
            && text(index + 1) == Some("!")
            && matches!(text(index + 2), Some("(" | "[" | "{"))
            && !STD_MACROS.contains(&token.text.as_str())
        {
            emit(&token.text, "macro", token.line, &mut out);
            index += 2;
            continue;
        }
        let after_dot = index
            .checked_sub(1)
            .is_some_and(|before| tokens[before].text == ".");
        if token.ident && text(index + 1) == Some("::") && text(index + 2) != Some("<") {
            // A path: walk its segments.
            let mut segments: Vec<&Token> = vec![token];
            let mut at = index + 1;
            while at + 1 < tokens.len() && tokens[at].text == "::" && tokens[at + 1].ident {
                segments.push(&tokens[at + 1]);
                at += 2;
            }
            // `module::item` names the item; `Type::item` is an associated
            // item (`Vec::new`, `Json::get`), which would match every `new`
            // in the project, so only the type counts there as a path. A
            // call of the associated item is a `method_call` (2.4), which
            // the edge builder links only when the name is defined once.
            let last = segments.len() - 1;
            // `name::<T>(..)` is a turbofish call of the last segment.
            let called =
                text(at) == Some("(") || (text(at) == Some("::") && text(at + 1) == Some("<"));
            for (position, segment) in segments.iter().enumerate() {
                let after_type = position.checked_sub(1).is_some_and(|before| {
                    capitalised(&segments[before].text) || segments[before].text == "Self"
                });
                let item_of_module = position == last && position > 0 && !after_type;
                if item_of_module || capitalised(&segment.text) {
                    emit(&segment.text, "path", segment.line, &mut out);
                } else if position == last && called && !KEYWORDS.contains(&segment.text.as_str()) {
                    let kind = if after_type { "method_call" } else { "call" };
                    emit(&segment.text, kind, segment.line, &mut out);
                }
            }
            index = at;
            continue;
        }
        let called = text(index + 1) == Some("(")
            || (text(index + 1) == Some("::") && text(index + 2) == Some("<"));
        if token.ident
            && called
            && !capitalised(&token.text)
            && !KEYWORDS.contains(&token.text.as_str())
            && !index
                .checked_sub(1)
                .is_some_and(|before| tokens[before].text == "fn")
        {
            // `.name(..)`, or `<T>::name(..)` after a turbofish.
            let after_path = index
                .checked_sub(1)
                .is_some_and(|before| tokens[before].text == "::");
            let kind = if after_dot || after_path {
                "method_call"
            } else {
                "call"
            };
            emit(&token.text, kind, token.line, &mut out);
            index += 1;
            continue;
        }
        if token.ident
            && capitalised(&token.text)
            && !defined_now.contains(&(token.text.clone(), token.line))
            && !after_dot
        {
            emit(&token.text, "type", token.line, &mut out);
        }
        index += 1;
    }
    out
}

/// The name a definition keyword at `index` introduces, and its kind:
/// `fn` (whatever qualifiers come before it), `struct`, `enum`, `trait`,
/// `union`, `type` aliases, `mod`, and `const` / `static` items. Const
/// generics (`<const N: usize>`) and `const fn` are not items here.
fn definition_at(tokens: &[Token], index: usize) -> Option<(&Token, &'static str)> {
    let token = &tokens[index];
    let next = tokens.get(index + 1).filter(|next| next.ident)?;
    let after = tokens.get(index + 2).map(|after| after.text.as_str());
    let before = index
        .checked_sub(1)
        .map(|before| tokens[before].text.as_str());
    match token.text.as_str() {
        "fn" => Some((next, "function")),
        "struct" | "enum" | "trait" => Some((next, "class")),
        "union" if matches!(after, Some("{" | "<")) => Some((next, "class")),
        "type" if matches!(after, Some("=" | "<")) && item_start(tokens, index) => {
            Some((next, "class"))
        }
        "mod" if matches!(after, Some("{" | ";")) => Some((next, "module")),
        "const" | "static" if !matches!(before, Some("<" | ",")) => {
            let (name, colon) = if next.text == "mut" {
                (tokens.get(index + 2).filter(|name| name.ident)?, index + 3)
            } else {
                (next, index + 2)
            };
            if KEYWORDS.contains(&name.text.as_str())
                || tokens.get(colon).map(|token| token.text.as_str()) != Some(":")
            {
                return None;
            }
            Some((name, "constant"))
        }
        _ => None,
    }
}

/// `type` starts an item, not an associated type in an `impl` or trait: it
/// is the first token of an unindented line, or follows `pub` / `pub(..)`.
fn item_start(tokens: &[Token], index: usize) -> bool {
    let token = &tokens[index];
    if token.first && token.column == 0 {
        return true;
    }
    match index
        .checked_sub(1)
        .map(|before| tokens[before].text.as_str())
    {
        Some("pub") => true,
        Some(")") => tokens[..index]
            .iter()
            .rposition(|token| token.text == "(")
            .and_then(|open| open.checked_sub(1))
            .is_some_and(|open| tokens[open].text == "pub"),
        _ => false,
    }
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
    /// The first token on its line, and its column there.
    first: bool,
    column: u32,
}

/// Identifiers, `::`, and single punctuation, with 1-based lines.
fn tokenize(code: &[char]) -> Vec<Token> {
    let mut tokens: Vec<Token> = Vec::new();
    let mut line = 1u32;
    let mut line_start = 0usize;
    let mut index = 0usize;
    let first_on = |tokens: &[Token], line: u32| tokens.last().is_none_or(|last| last.line != line);
    while index < code.len() {
        let ch = code[index];
        if ch == '\n' {
            line += 1;
            index += 1;
            line_start = index;
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
                first: first_on(&tokens, line),
                column: (start - line_start) as u32,
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
                first: first_on(&tokens, line),
                column: (index - line_start) as u32,
                text: "::".to_string(),
                ident: false,
                line,
            });
            index += 2;
            continue;
        }
        tokens.push(Token {
            first: first_on(&tokens, line),
            column: (index - line_start) as u32,
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
pub(crate) fn strip(content: &str) -> Vec<char> {
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
        // `new` of `Vec::<u8>::new()` is an associated call, never a path.
        assert!(
            rows.iter()
                .all(|(name, kind, _)| name != "new" || kind == "method_call"),
            "{rows:?}"
        );
    }

    fn kinds(content: &str) -> Vec<(String, String, String, u32)> {
        let base = super::super::extract("rs", content);
        let mut rows: Vec<(String, String, String, u32)> = extra(content, &base)
            .into_iter()
            .map(|hit| (hit.name, hit.kind, hit.role, hit.line))
            .collect();
        rows.sort();
        rows
    }

    fn has(
        rows: &[(String, String, String, u32)],
        name: &str,
        kind: &str,
        role: &str,
        line: u32,
    ) -> bool {
        rows.iter()
            .any(|row| row.0 == name && row.1 == kind && row.2 == role && row.3 == line)
    }

    #[test]
    fn calls_methods_macros_and_closures_are_references() {
        // Specula's false "unreferenced": a private fn called by bare name
        // in the same file, inside `if` and `&&`, as an argument, and in a
        // closure and a macro.
        let content = "fn human_took_controls(input: &[Event]) -> bool { false }
                       fn game_frame(&mut self) {
                           if self.follow.is_some() && human_took_controls(&self.input) {}
                           let f = |e| helper_in_closure(e);
                           assert!(inside_macro(1));
                           self.board.world_replaced();
                           let v = parse::<u32>(text);
                           let b = Board::from_save(&bytes);
                           my_macro!(x);
                           println!(\"{}\", 1);
                           // commented_call(1)
                           if (a) {} while (b) {} match (c) {}
                       }
";
        let rows = kinds(content);
        assert!(
            has(&rows, "human_took_controls", "call", "reference", 3),
            "{rows:?}"
        );
        assert!(
            has(&rows, "helper_in_closure", "call", "reference", 4),
            "{rows:?}"
        );
        assert!(
            has(&rows, "inside_macro", "call", "reference", 5),
            "{rows:?}"
        );
        assert!(
            has(&rows, "world_replaced", "method_call", "reference", 6),
            "{rows:?}"
        );
        assert!(has(&rows, "parse", "call", "reference", 7), "{rows:?}");
        assert!(
            has(&rows, "from_save", "method_call", "reference", 8),
            "{rows:?}"
        );
        assert!(has(&rows, "my_macro", "macro", "reference", 9), "{rows:?}");
        for not in [
            "println",
            "assert",
            "commented_call",
            "if",
            "while",
            "match",
            "game_frame",
        ] {
            assert!(
                rows.iter().all(|row| row.0 != not || row.2 == "definition"),
                "{not}: {rows:?}"
            );
        }
    }

    #[test]
    fn definitions_the_line_patterns_miss() {
        let content = "#[macro_export]
                       macro_rules! export_module { ($x:expr) => { $x }; }
                       pub(crate) fn game_rolled_back(&mut self) {}
                       pub(super) const fn packed() -> u32 { 0 }
                       unsafe fn raw() {}
                       pub extern \"C\" fn ffi() {}
                       pub(crate) struct Inner;
                       pub union Bits { a: u32 }
                       pub type Grid<T> = Vec<T>;
                       impl Iterator for It {
    type Item = u32;
}
                       mod nested { }
                       pub const MAX_LANES: usize = 8;
                       static mut COUNTER: u32 = 0;
                       struct Arr<const N: usize>;
                       const _: () = ();
";
        let rows = kinds(content);
        assert!(
            has(&rows, "export_module", "macro", "definition", 2),
            "{rows:?}"
        );
        assert!(
            has(&rows, "game_rolled_back", "function", "definition", 3),
            "{rows:?}"
        );
        assert!(
            has(&rows, "packed", "function", "definition", 4),
            "{rows:?}"
        );
        assert!(has(&rows, "raw", "function", "definition", 5), "{rows:?}");
        assert!(has(&rows, "ffi", "function", "definition", 6), "{rows:?}");
        assert!(has(&rows, "Inner", "class", "definition", 7), "{rows:?}");
        assert!(has(&rows, "Bits", "class", "definition", 8), "{rows:?}");
        assert!(has(&rows, "Grid", "class", "definition", 9), "{rows:?}");
        assert!(has(&rows, "nested", "module", "definition", 13), "{rows:?}");
        assert!(
            has(&rows, "MAX_LANES", "constant", "definition", 14),
            "{rows:?}"
        );
        assert!(
            has(&rows, "COUNTER", "constant", "definition", 15),
            "{rows:?}"
        );
        // An associated type, a const generic and `const _` are not items;
        // `Inner` on its definition line is not a reference to itself.
        for not in ["Item", "N", "_"] {
            assert!(
                rows.iter().all(|row| row.0 != not || row.2 != "definition"),
                "{not}: {rows:?}"
            );
        }
        assert!(
            rows.iter()
                .all(|row| !(row.0 == "Inner" && row.2 == "reference")),
            "{rows:?}"
        );
        // 1.5.2's own patterns already give `fn raw` once: no duplicate.
        let all = super::super::extract_all("rs", content);
        assert_eq!(
            all.iter()
                .filter(|hit| hit.name == "raw" && hit.role == "definition")
                .count(),
            1
        );
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
