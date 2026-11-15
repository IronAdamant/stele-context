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
//! - (2.5) the named fields of structs, unions and enum variants
//!   (definitions of kind `field`, `container` the owning type: `Index`, or
//!   `Shape::Circle` for a variant), `.name` not followed by `(` (kind
//!   `field_access`), and a field named in a struct literal or pattern
//!   (`Index { tasks: x }`, `Index { tasks, .. }`: kind `struct_field`).
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
    let code: String = strip(content).into_iter().collect();
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
                  container: Option<String>,
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
            container,
        });
    };
    let enclosing = enclosing_opens(&tokens);
    let text = |at: usize| tokens.get(at).map(|token| token.text);
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
                define(
                    &name.text,
                    kind,
                    name.line,
                    None,
                    &mut out,
                    &mut defined_now,
                );
                // 2.5: the named fields of a struct, union or enum variant.
                if matches!(token.text, "struct" | "union" | "enum") {
                    for (field, owner) in fields_of(&tokens, index) {
                        define(
                            &field.text,
                            "field",
                            field.line,
                            Some(owner),
                            &mut out,
                            &mut defined_now,
                        );
                    }
                }
            }
        }
        if token.ident && token.text == "macro_rules" && text(index + 1) == Some("!") {
            if let Some(name) = tokens.get(index + 2).filter(|name| name.ident) {
                define(
                    &name.text,
                    "macro",
                    name.line,
                    None,
                    &mut out,
                    &mut defined_now,
                );
                index += 3;
                continue;
            }
        }
        if token.ident
            && text(index + 1) == Some("!")
            && matches!(text(index + 2), Some("(" | "[" | "{"))
            && !STD_MACROS.contains(&token.text)
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
                } else if position == last && called && !KEYWORDS.contains(&segment.text) {
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
            && !KEYWORDS.contains(&token.text)
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
        // 2.5: `.name` not called is a field access; a field named in a
        // struct literal or pattern is a `struct_field`.
        if token.ident && !KEYWORDS.contains(&token.text) {
            if field_after_dot(&tokens, index) {
                emit(&token.text, "field_access", token.line, &mut out);
                index += 1;
                continue;
            }
            if !defined_now.contains(&(token.text.to_string(), token.line))
                && literal_field(&tokens, &enclosing, index).is_some()
            {
                emit(&token.text, "struct_field", token.line, &mut out);
                index += 1;
                continue;
            }
        }
        if token.ident
            && capitalised(&token.text)
            && !defined_now.contains(&(token.text.to_string(), token.line))
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
fn definition_at<'a>(
    tokens: &'a [Token<'a>],
    index: usize,
) -> Option<(&'a Token<'a>, &'static str)> {
    let token = &tokens[index];
    let next = tokens.get(index + 1).filter(|next| next.ident)?;
    let after = tokens.get(index + 2).map(|after| after.text);
    let before = index.checked_sub(1).map(|before| tokens[before].text);
    match token.text {
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
            if KEYWORDS.contains(&name.text)
                || tokens.get(colon).map(|token| token.text) != Some(":")
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
    match index.checked_sub(1).map(|before| tokens[before].text) {
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

/// For each token, the index of the innermost `{`, `(` or `[` still open
/// before it.
fn enclosing_opens(tokens: &[Token]) -> Vec<Option<usize>> {
    let mut stack: Vec<usize> = Vec::new();
    let mut out = Vec::with_capacity(tokens.len());
    for (at, token) in tokens.iter().enumerate() {
        match token.text {
            "}" | ")" | "]" => {
                stack.pop();
                out.push(stack.last().copied());
            }
            "{" | "(" | "[" => {
                out.push(stack.last().copied());
                stack.push(at);
            }
            _ => out.push(stack.last().copied()),
        }
    }
    out
}

/// The `{` that opens the body of the item whose keyword is at `keyword`
/// (`struct`, `enum`, `union`, `impl`), past generics and a `where`
/// clause; `None` for a tuple or unit struct.
fn body_open(tokens: &[Token], keyword: usize) -> Option<usize> {
    let mut angle = 0i32;
    for at in keyword + 1..tokens.len() {
        match tokens[at].text {
            "<" => angle += 1,
            ">" if !(at > 0 && tokens[at - 1].text == "-") => angle -= 1,
            "{" if angle <= 0 => return Some(at),
            "(" | ";" if angle <= 0 => return None,
            _ => {}
        }
    }
    None
}

/// The named fields declared between the `{` at `open` and its `}`: an
/// identifier followed by `:` at the top level of the braces, after `{`,
/// `,`, `pub`, `pub(..)` or an attribute.
fn named_fields<'a>(tokens: &'a [Token<'a>], open: usize) -> Vec<&'a Token<'a>> {
    let mut out = Vec::new();
    let mut depth = 0i32;
    let mut at = open + 1;
    while at < tokens.len() {
        match tokens[at].text {
            "{" | "(" | "[" => depth += 1,
            "}" | ")" | "]" => {
                if depth == 0 {
                    break;
                }
                depth -= 1;
            }
            _ => {}
        }
        let token = &tokens[at];
        if depth == 0
            && token.ident
            && !KEYWORDS.contains(&token.text)
            && tokens.get(at + 1).is_some_and(|next| next.text == ":")
            && matches!(tokens[at - 1].text, "{" | "," | "pub" | ")" | "]")
        {
            out.push(token);
        }
        at += 1;
    }
    out
}

/// The named fields of the struct, union or enum whose keyword is at
/// `keyword`, each with its owner: the type, or `Enum::Variant`.
fn fields_of<'a>(tokens: &'a [Token<'a>], keyword: usize) -> Vec<(&'a Token<'a>, String)> {
    let Some(name) = tokens.get(keyword + 1) else {
        return Vec::new();
    };
    let Some(open) = body_open(tokens, keyword) else {
        return Vec::new();
    };
    if tokens[keyword].text != "enum" {
        return named_fields(tokens, open)
            .into_iter()
            .map(|field| (field, name.text.to_string()))
            .collect();
    }
    let mut out = Vec::new();
    let mut depth = 0i32;
    let mut at = open + 1;
    while at < tokens.len() {
        let token = &tokens[at];
        if depth == 0
            && token.ident
            && matches!(tokens[at - 1].text, "{" | "," | "]")
            && tokens.get(at + 1).is_some_and(|next| next.text == "{")
        {
            let owner = format!("{}::{}", name.text, token.text);
            for field in named_fields(tokens, at + 1) {
                out.push((field, owner.clone()));
            }
        }
        match token.text {
            "{" | "(" | "[" => depth += 1,
            "}" | ")" | "]" => {
                if depth == 0 {
                    break;
                }
                depth -= 1;
            }
            _ => {}
        }
        at += 1;
    }
    out
}

/// `.name` not called (`.name(..)`, `.name::<T>(..)`) and not after a
/// range's `..`.
fn field_after_dot(tokens: &[Token], index: usize) -> bool {
    let text = |at: usize| tokens.get(at).map(|token| token.text);
    index >= 1
        && text(index - 1) == Some(".")
        && !(index >= 2 && text(index - 2) == Some("."))
        && text(index + 1) != Some("(")
        && !(text(index + 1) == Some("::") && text(index + 2) == Some("<"))
        && text(index + 1) != Some("!")
}

/// The type a struct literal or pattern names when the identifier at
/// `index` is one of its fields (`Index { tasks: x }`, `Self { tasks, .. }`):
/// the written path's last type (`Index`, `Self`, `Shape::Circle`).
fn literal_field(tokens: &[Token], enclosing: &[Option<usize>], index: usize) -> Option<String> {
    let token = &tokens[index];
    if !token.ident || capitalised(&token.text) || index == 0 {
        return None;
    }
    if !matches!(tokens[index - 1].text, "{" | ",") {
        return None;
    }
    if !tokens
        .get(index + 1)
        .is_some_and(|next| matches!(next.text, ":" | "," | "}"))
    {
        return None;
    }
    let open = enclosing.get(index).copied().flatten()?;
    if tokens[open].text != "{" {
        return None;
    }
    literal_type(tokens, open)
}

/// The path before the `{` at `open` when that brace opens a struct
/// literal or pattern, not an item body or a block.
fn literal_type(tokens: &[Token], open: usize) -> Option<String> {
    let mut at = open.checked_sub(1)?;
    let last = &tokens[at];
    if !last.ident || !(capitalised(&last.text) || last.text == "Self") {
        return None;
    }
    let mut segments = vec![last.text];
    while at >= 2 && tokens[at - 1].text == "::" && tokens[at - 2].ident {
        at -= 2;
        segments.push(tokens[at].text);
    }
    let before = at.checked_sub(1).map(|before| tokens[before].text);
    let before_two = at.checked_sub(2).map(|before| tokens[before].text);
    match before {
        // Item headers, bounds, `-> Type {` and `for x in Type {`.
        Some(
            "struct" | "enum" | "union" | "impl" | "for" | "trait" | "dyn" | ":" | ">" | "+"
            | "mod" | "where" | "in" | "type" | "fn" | "if" | "while" | "match",
        ) => return None,
        // `if a == Mode {` and `if a && Flag {`: a block.
        Some("=") if matches!(before_two, Some("=" | "!" | "<" | ">")) => return None,
        Some("&") if before_two == Some("&") => return None,
        _ => {}
    }
    let typed: Vec<&str> = segments
        .iter()
        .copied()
        .filter(|segment| capitalised(segment) || *segment == "Self")
        .collect();
    Some(match typed.as_slice() {
        [variant, owner, ..] => format!("{owner}::{variant}"),
        [only] => only.to_string(),
        [] => return None,
    })
}

/// The type an `impl` at `index` is for (`impl Index`, `impl<T> Trait for
/// Index<T>`), when `index` starts an impl item and not `-> impl Trait`.
fn impl_header(tokens: &[Token], index: usize) -> Option<(usize, String)> {
    if let Some(before) = index.checked_sub(1) {
        if !matches!(
            tokens[before].text,
            "}" | ";" | "]" | "{" | "unsafe" | "default"
        ) {
            return None;
        }
    }
    let open = body_open(tokens, index)?;
    let mut angle = 0i32;
    let mut start = index + 1;
    let mut end = open;
    for at in index + 1..open {
        match tokens[at].text {
            "<" => angle += 1,
            ">" if tokens[at - 1].text != "-" => angle -= 1,
            "for" if angle <= 0 => start = at + 1,
            "where" if angle <= 0 => {
                end = at;
                break;
            }
            _ => {}
        }
    }
    let mut angle = 0i32;
    let mut owner = None;
    for at in start..end {
        let token = &tokens[at];
        match token.text {
            "<" => angle += 1,
            ">" if tokens[at - 1].text != "-" => angle -= 1,
            _ if angle <= 0 && token.ident && capitalised(&token.text) => {
                owner = Some(token.text.to_string())
            }
            _ => {}
        }
    }
    Some((open, owner?))
}

/// Std methods that change the collection or cell they are called on.
const MUTATORS: &[&str] = &[
    "append",
    "clear",
    "dedup",
    "drain",
    "entry",
    "extend",
    "extend_from_slice",
    "fill",
    "get_mut",
    "insert",
    "iter_mut",
    "pop",
    "pop_back",
    "pop_front",
    "push",
    "push_back",
    "push_front",
    "push_str",
    "remove",
    "replace",
    "resize",
    "retain",
    "retain_mut",
    "reverse",
    "set",
    "sort",
    "sort_by",
    "sort_by_key",
    "sort_unstable",
    "split_off",
    "swap",
    "swap_remove",
    "take",
    "truncate",
    "values_mut",
];

/// The field access at `index` changes the field: it is assigned
/// (`=`, `+=` ...), borrowed `&mut`, or a mutating std method is called on
/// it (`.push(..)`, `.remove(..)`, `.retain(..)` ...).
fn writes_field(tokens: &[Token], index: usize) -> bool {
    let text = |at: usize| tokens.get(at).map(|token| token.text);
    let next = text(index + 1);
    let after = text(index + 2);
    if next == Some("=") && !matches!(after, Some("=" | ">")) {
        return true;
    }
    if matches!(next, Some("+" | "-" | "*" | "/" | "%" | "^" | "|" | "&")) && after == Some("=") {
        return true;
    }
    if matches!(next, Some("<" | ">")) && after == next && text(index + 3) == Some("=") {
        return true;
    }
    if next == Some(".")
        && after.is_some_and(|method| MUTATORS.contains(&method))
        && text(index + 3) == Some("(")
    {
        return true;
    }
    index >= 4
        && text(index - 1) == Some(".")
        && text(index - 3) == Some("mut")
        && text(index - 4) == Some("&")
}

/// One place a Rust file's code names a word (2.5): its 1-based line, how
/// it uses it, the owning type where the code says it, and whether a field
/// access changes the field.
#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct Use {
    pub line: u32,
    pub kind: &'static str,
    pub owner: Option<String>,
    pub write: bool,
}

/// Every place the code of `content` (a whole Rust file, or one chunk)
/// names `name` as a word, comments and strings skipped. `kind` is `macro`
/// (`name!(..)`), `method_call` (`.name(..)`, `Type::name(..)`), `call`,
/// `field_access` (`.name`), `struct_field` (`Type { name: .. }`) or
/// `mention`. `owner` is set where the code says the type without
/// inference: `self.name` and `Self { name }` inside `impl Type`, and
/// `Type { name }`.
pub(crate) fn uses(content: &str, name: &str) -> Vec<Use> {
    let code: String = strip(content).into_iter().collect();
    let tokens = tokenize(&code);
    let enclosing = enclosing_opens(&tokens);
    let text = |at: usize| tokens.get(at).map(|token| token.text);
    // The type of each open impl body, by its `{`.
    let mut impl_bodies: Vec<(usize, String)> = Vec::new();
    let mut stack: Vec<(usize, Option<String>)> = Vec::new();
    let mut out = Vec::new();
    for (index, token) in tokens.iter().enumerate() {
        match token.text {
            "{" => {
                let owner = impl_bodies
                    .iter()
                    .position(|(open, _)| *open == index)
                    .map(|at| impl_bodies.swap_remove(at).1);
                stack.push((index, owner));
                continue;
            }
            "}" => {
                stack.pop();
                continue;
            }
            "impl" => {
                if let Some(header) = impl_header(&tokens, index) {
                    impl_bodies.push(header);
                }
                continue;
            }
            _ => {}
        }
        if !token.ident || token.text != name {
            continue;
        }
        let in_impl = || stack.iter().rev().find_map(|(_, owner)| owner.clone());
        let after_dot = index >= 1 && text(index - 1) == Some(".");
        let after_path = index >= 1 && text(index - 1) == Some("::");
        let called = text(index + 1) == Some("(")
            || (text(index + 1) == Some("::") && text(index + 2) == Some("<"));
        let (kind, owner, write) =
            if text(index + 1) == Some("!") && matches!(text(index + 2), Some("(" | "[" | "{")) {
                ("macro", None, false)
            } else if called {
                let kind = if after_dot || after_path {
                    "method_call"
                } else {
                    "call"
                };
                (kind, None, false)
            } else if field_after_dot(&tokens, index) {
                let owner = if index >= 2 && text(index - 2) == Some("self") {
                    in_impl()
                } else {
                    None
                };
                ("field_access", owner, writes_field(&tokens, index))
            } else if let Some(written) = literal_field(&tokens, &enclosing, index) {
                let owner = if written == "Self" || written.starts_with("Self::") {
                    in_impl().map(|owner| format!("{owner}{}", &written[4..]))
                } else {
                    Some(written)
                };
                ("struct_field", owner, false)
            } else {
                ("mention", None, false)
            };
        out.push(Use {
            line: token.line,
            kind,
            owner,
            write,
        });
    }
    out
}

/// The names a `use` tree imports, from `start` (just after `use`) to its
/// `;`. Returns the index after the `;`.
fn use_tree(tokens: &[Token], start: usize, found: &mut dyn FnMut(&str, u32)) -> usize {
    let mut index = start;
    let mut depth = 0i32;
    while index < tokens.len() {
        let token = &tokens[index];
        match token.text {
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
                let next = tokens.get(index + 1).map(|next| next.text);
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

struct Token<'a> {
    text: &'a str,
    ident: bool,
    line: u32,
    /// The first token on its line, and its column there.
    first: bool,
    column: u32,
}

/// Identifiers, `::`, and single punctuation, with 1-based lines. Token
/// text borrows `code` (2.5: no allocation per token).
fn tokenize(code: &str) -> Vec<Token<'_>> {
    let chars: Vec<(usize, char)> = code.char_indices().collect();
    let byte = |at: usize| {
        chars
            .get(at)
            .map(|(offset, _)| *offset)
            .unwrap_or(code.len())
    };
    let mut tokens: Vec<Token> = Vec::with_capacity(chars.len() / 4);
    let mut line = 1u32;
    let mut line_start = 0usize;
    let mut index = 0usize;
    let first_on = |tokens: &[Token], line: u32| tokens.last().is_none_or(|last| last.line != line);
    while index < chars.len() {
        let ch = chars[index].1;
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
            while index < chars.len() && (chars[index].1.is_alphanumeric() || chars[index].1 == '_')
            {
                index += 1;
            }
            // `r#name` raw identifiers.
            let text = &code[byte(start)..byte(index)];
            if text == "r" && chars.get(index).map(|(_, ch)| *ch) == Some('#') {
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
            while index < chars.len() && (chars[index].1.is_alphanumeric() || chars[index].1 == '_')
            {
                index += 1;
            }
            continue;
        }
        let width = if ch == ':' && chars.get(index + 1).map(|(_, ch)| *ch) == Some(':') {
            2
        } else {
            1
        };
        tokens.push(Token {
            first: first_on(&tokens, line),
            column: (index - line_start) as u32,
            text: &code[byte(index)..byte(index + width)],
            ident: false,
            line,
        });
        index += width;
    }
    tokens
}

/// The chunk with comments and string and char literals blanked (newlines
/// kept, so line numbers hold).
pub(crate) fn strip(content: &str) -> Vec<char> {
    let (chars, regions) = literal_regions(content);
    let mut out = chars;
    for (from, to, _) in regions {
        blank(&mut out, from, to);
    }
    out
}

/// The chunk with everything but string literals blanked (newlines kept):
/// the text a scan for a name used as a string reads (2.5 `named_in`).
pub(crate) fn strings_only(content: &str) -> Vec<char> {
    let (chars, regions) = literal_regions(content);
    let mut out: Vec<char> = chars
        .iter()
        .map(|ch| if *ch == '\n' { '\n' } else { ' ' })
        .collect();
    for (from, to, string) in regions {
        if string {
            let to = to.min(chars.len());
            out[from..to].copy_from_slice(&chars[from..to]);
        }
    }
    out
}

fn blank(out: &mut [char], from: usize, to: usize) {
    for slot in out.iter_mut().take(to).skip(from) {
        if *slot != '\n' {
            *slot = ' ';
        }
    }
}

/// The characters of `content` and its comment and literal regions as
/// `(from, to, is_string)`; char literals count as strings.
fn literal_regions(content: &str) -> (Vec<char>, Vec<(usize, usize, bool)>) {
    let chars: Vec<char> = content.chars().collect();
    let mut regions: Vec<(usize, usize, bool)> = Vec::new();
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
            regions.push((index, end, false));
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
            regions.push((index, at.min(chars.len()), false));
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
                regions.push((index, end.min(chars.len()), true));
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
            regions.push((index, end, true));
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
                regions.push((index, close + 1, true));
                index = close + 1;
                continue;
            }
        }
        index += 1;
    }
    (chars, regions)
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
                       use sample_core::checksum::crc32;\n\
                       // WorldError in a comment, \"Opener\" in text\n\
                       pub fn over(world: &mut dyn crate::write_policy::Writer) -> Box<dyn Shape> {\n\
                           let x = 'a'; let y: &'static str = \"Mark\";\n\
                           sample_core::checksum::crc32_update(x, y)\n\
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
        // The false "unreferenced" on a large Rust workspace: a private fn called by bare name
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

    /// 2.5: on a large Rust workspace, "every place a reference is added or dropped" on four
    /// fields took five greps: fields were not symbols.
    #[test]
    fn struct_and_variant_fields_are_definitions_and_uses_are_references() {
        let content = "pub struct Index<T: Clone> where T: Default {
    pub tasks: Vec<T>,
    #[allow(dead_code)]
    pub(crate) notes: Vec<String>,
    f: fn(u32) -> u32,
}
pub enum Shape { Circle { radius: f32 }, Unit, Pair(u32, u32) }
pub struct Tuple(u32);
impl<T: Clone + Default> Index<T> {
    fn add(&mut self, t: T) {
        self.tasks.push(t);
        let n = 0..self.notes.len();
        let Index { tasks, .. } = self;
        let s = Shape::Circle { radius: 1.0 };
        let r = s.radius;
    }
}
";
        let rows = kinds(content);
        for (name, line) in [("tasks", 2), ("notes", 4), ("f", 5), ("radius", 7)] {
            assert!(
                has(&rows, name, "field", "definition", line),
                "{name}: {rows:?}"
            );
        }
        let all = super::super::extract_all("rs", content);
        let owner = |name: &str| {
            all.iter()
                .find(|hit| hit.name == name && hit.kind == "field")
                .and_then(|hit| hit.container.clone())
        };
        assert_eq!(owner("tasks").as_deref(), Some("Index"));
        assert_eq!(owner("radius").as_deref(), Some("Shape::Circle"));
        // `self.tasks` is the first use of `tasks` in the chunk; one row per
        // name and chunk, as for every other reference.
        assert!(
            has(&rows, "tasks", "field_access", "reference", 11),
            "{rows:?}"
        );
        assert!(
            has(&rows, "notes", "field_access", "reference", 12),
            "{rows:?}"
        );
        assert!(
            has(&rows, "radius", "struct_field", "reference", 14),
            "{rows:?}"
        );
        // Not fields: a where bound, a tuple struct, a method, a variant
        // without fields, a fn type's argument.
        for not in ["T", "Tuple", "push", "len", "Unit", "u32"] {
            assert!(
                rows.iter().all(|row| row.0 != not || row.1 != "field"),
                "{not}: {rows:?}"
            );
        }
        assert!(
            rows.iter()
                .all(|row| !(row.1 == "field_access" && (row.0 == "len" || row.0 == "push"))),
            "{rows:?}"
        );
    }

    #[test]
    fn uses_name_the_owner_where_the_code_writes_it() {
        let content = "struct Index { tasks: Vec<u32> }
struct Other { tasks: u32 }
impl Index {
    fn add(&mut self, t: u32) {
        self.tasks.push(t);
        self.tasks = Vec::new();
        let n = self.tasks.len();
        let x = other.tasks;
        let me = Self { tasks: Vec::new() };
        let o = Other { tasks: 1 };
        borrow(&mut self.tasks);
        tasks(1);
        if self.tasks == x {}
    }
}
fn build() -> Index { Index { tasks } }
fn range(v: &[u32]) { let s = &v[0..tasks]; }
// self.tasks in a comment
";
        let uses = uses(content, "tasks");
        let at = |line: u32| {
            uses.iter()
                .find(|one| one.line == line)
                .unwrap_or_else(|| panic!("line {line}: {uses:?}"))
        };
        let owned = |line: u32, kind: &str, owner: Option<&str>, write: bool| {
            let one = at(line);
            assert_eq!(
                (one.kind, one.owner.as_deref(), one.write),
                (kind, owner, write),
                "line {line}: {uses:?}"
            );
        };
        owned(5, "field_access", Some("Index"), true);
        owned(6, "field_access", Some("Index"), true);
        owned(7, "field_access", Some("Index"), false);
        owned(8, "field_access", None, false);
        owned(9, "struct_field", Some("Index"), false);
        owned(10, "struct_field", Some("Other"), false);
        owned(11, "field_access", Some("Index"), true);
        owned(12, "call", None, false);
        owned(13, "field_access", Some("Index"), false);
        // `-> Index { Index { tasks } }`: the body brace is not a literal.
        owned(16, "struct_field", Some("Index"), false);
        owned(17, "mention", None, false);
        assert!(uses.iter().all(|one| one.line != 18), "{uses:?}");
    }

    #[test]
    fn strings_only_keeps_string_literals() {
        let text: String =
            strings_only("let a = \"model_export\"; // model_export\nmodel_export(1);\n")
                .into_iter()
                .collect();
        assert!(text.contains("\"model_export\""), "{text:?}");
        assert_eq!(text.matches("model_export").count(), 1, "{text:?}");
        assert_eq!(text.lines().count(), 2);
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
