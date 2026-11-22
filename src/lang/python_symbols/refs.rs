//! Python name uses that CPython's `ast` walk (and so 1.5.2) does not
//! record as symbols (2.6). `find_references` adds them for the chunks that
//! hold the name (`engine::python_mentions`); they are not index rows.
//!
//! The walk records a Name load and a called attribute, so `exp.Name(..)`
//! is a reference and `exp.Name` is not: a dict key (`exp.Name: render`),
//! an annotation (`-> exp.Name`), `exp.Name.from_arg_list` and a tuple
//! element were all missed, and a chunk that is not a valid module kept
//! only its definitions and imports. This pass reads the tokens and adds,
//! once per name and line not already recorded:
//! - every attribute name (`a.b.Name`: `b` and `Name`, kind `attribute`);
//! - every bare name (kind `name`), assignment targets included;
//! - names imported on a line of their own inside `from m import (..)`.
//!
//! Left out: the name a `def` or `class` defines, parameter names, the
//! name after `as`, keyword-argument names (`f(name=1)`), the module path
//! of an import, `global`/`nonlocal` lists, keywords, strings and comments
//! (the lexer keeps those as their own tokens). In a valid module a called
//! name is skipped: the walk already has it, at the call's first line.

use super::is_keyword;
use crate::lang::kind;
use crate::syntax::cst::Token;
use crate::syntax::query::SymbolHit;
use std::collections::HashSet;

#[derive(Clone, Copy, PartialEq, Eq)]
enum Import {
    None,
    /// `import a.b` or the `m.n` of `from m.n import`: module paths.
    Module,
    /// The names after `from m import`.
    Names,
}

struct Scope {
    /// The indent level of the body.
    level: i32,
    name: String,
    /// The indented body has started (a one-line body has none).
    body: bool,
    function: bool,
}

/// Adds the uses to `hits` (the walk's rows, or the regex fallback's when
/// `valid` is false). A name of `from m import (..)` on a line of its own
/// moves the walk's row (at the statement line, as `ast` gives it) there.
pub(super) fn extend(source: &str, tokens: &[Token], hits: &mut Vec<SymbolHit>, valid: bool) {
    let mut seen: HashSet<(String, u32)> = hits
        .iter()
        .map(|hit| (hit.name.clone(), hit.line))
        .collect();
    let walked = hits.len();
    let text = |token: &Token| -> &str {
        let start = token.start as usize;
        let end = (token.end as usize).min(source.len());
        source.get(start..end).unwrap_or("")
    };
    let is_op = |at: usize, word: &str| {
        tokens
            .get(at)
            .is_some_and(|token| token.kind == kind::OP && text(token) == word)
    };
    let mut line = 1u32;
    let mut import_line = 0u32;
    let mut brackets: Vec<u8> = Vec::new();
    let mut level = 0i32;
    let mut scopes: Vec<Scope> = Vec::new();
    // The previous token that is not whitespace or a comment (a newline
    // inside brackets is not one either).
    let mut prev: Option<usize> = None;
    let mut import = Import::None;
    let mut skip_line = false;
    let mut after_as = false;
    let mut params_depth: Option<usize> = None;
    let mut lambda_depth: Option<usize> = None;
    let mut pending_params = false;
    for (at, token) in tokens.iter().enumerate() {
        let token_line = line;
        line += text(token).bytes().filter(|byte| *byte == b'\n').count() as u32;
        match token.kind {
            kind::WHITESPACE | kind::COMMENT => continue,
            kind::NEWLINE => {
                if !brackets.is_empty() {
                    continue;
                }
                import = Import::None;
                skip_line = false;
                after_as = false;
                lambda_depth = None;
                pending_params = false;
                if scopes.last().is_some_and(|scope| !scope.body) {
                    // A header ends here: an indented body follows, or the
                    // body was on this line (`def f(): return 1`).
                    if next_kind(tokens, at + 1) == Some(kind::INDENT) {
                        if let Some(scope) = scopes.last_mut() {
                            scope.body = true;
                        }
                    } else {
                        scopes.pop();
                    }
                }
                prev = Some(at);
                continue;
            }
            kind::INDENT => {
                level += 1;
                prev = Some(at);
                continue;
            }
            kind::DEDENT => {
                level -= 1;
                while scopes.last().is_some_and(|scope| scope.level > level) {
                    scopes.pop();
                }
                prev = Some(at);
                continue;
            }
            kind::OP => {
                let op = text(token);
                match op {
                    "(" | "[" | "{" => {
                        brackets.push(op.as_bytes()[0]);
                        if op == "(" && pending_params {
                            params_depth = Some(brackets.len());
                            pending_params = false;
                        }
                    }
                    ")" | "]" | "}" => {
                        if params_depth == Some(brackets.len()) {
                            params_depth = None;
                        }
                        brackets.pop();
                    }
                    ":" if lambda_depth == Some(brackets.len()) => lambda_depth = None,
                    ";" if brackets.is_empty() => {
                        import = Import::None;
                        skip_line = false;
                    }
                    _ => {}
                }
                prev = Some(at);
                continue;
            }
            kind::IDENT => {}
            _ => {
                prev = Some(at);
                continue;
            }
        }
        let name = text(token);
        let before = prev;
        let prev_text = prev.map(|index| text(&tokens[index])).unwrap_or("");
        let prev_kind = prev.map(|index| tokens[index].kind);
        let statement_start = match prev {
            None => true,
            Some(index) => {
                matches!(
                    tokens[index].kind,
                    kind::NEWLINE | kind::INDENT | kind::DEDENT
                ) || (tokens[index].kind == kind::OP && prev_text == ";")
            }
        };
        prev = Some(at);
        let attribute = prev_kind == Some(kind::OP) && prev_text == ".";
        if !attribute {
            if statement_start && (name == "import" || name == "from") {
                import = Import::Module;
                import_line = token_line;
                continue;
            }
            if import == Import::Module && name == "import" {
                import = Import::Names;
                continue;
            }
            if statement_start && (name == "global" || name == "nonlocal") {
                skip_line = true;
                continue;
            }
            if name == "as" {
                after_as = true;
                continue;
            }
            if name == "lambda" {
                lambda_depth = Some(brackets.len());
                continue;
            }
            if prev_kind == Some(kind::IDENT) && (prev_text == "def" || prev_text == "class") {
                scopes.push(Scope {
                    level: level + 1,
                    name: name.to_string(),
                    body: false,
                    function: prev_text == "def",
                });
                pending_params = prev_text == "def";
                continue;
            }
            if statement_start
                && matches!(name, "match" | "case" | "type")
                && matches!(
                    next_kind(tokens, at + 1),
                    Some(kind::IDENT | kind::STRING | kind::NUMBER)
                )
            {
                continue;
            }
        }
        if std::mem::take(&mut after_as) || skip_line || import == Import::Module {
            continue;
        }
        if is_keyword(name) || name == "_" {
            continue;
        }
        let next = next_real(tokens, at + 1, !brackets.is_empty());
        if params_depth == Some(brackets.len())
            && (prev_text == "(" || prev_text == "," || prev_text == "*" || prev_text == "**")
            && prev_kind == Some(kind::OP)
        {
            continue;
        }
        if lambda_depth.is_some() {
            continue;
        }
        if brackets.last() == Some(&b'(') && next.is_some_and(|index| is_op(index, "=")) {
            continue;
        }
        // A valid module's walk has every call and every bare name it
        // loads; what is left there is a binding. A module or class one
        // (`TRANSFORMS = {..}` in a class body) is kept, a function's
        // locals (`x = 1`, `for x in`) are not.
        if valid
            && (next.is_some_and(|index| is_op(index, "("))
                || (!attribute
                    && import != Import::Names
                    && scopes.iter().any(|scope| scope.function)))
        {
            continue;
        }
        if conversion_or_format(tokens, before, next, name, &text) {
            continue;
        }
        if import == Import::Names && token_line != import_line {
            let moved = hits[..walked]
                .iter_mut()
                .find(|hit| hit.kind == "import" && hit.line == import_line && hit.name == name);
            if let Some(hit) = moved {
                hit.line = token_line;
                seen.insert((name.to_string(), token_line));
                continue;
            }
        }
        if !seen.insert((name.to_string(), token_line)) {
            continue;
        }
        let kind_name = if attribute {
            "attribute"
        } else if import == Import::Names {
            "import"
        } else {
            "name"
        };
        hits.push(SymbolHit {
            name: name.to_string(),
            kind: super::intern_kind(kind_name),
            role: "reference",
            line: token_line,
            container: if scopes.is_empty() {
                None
            } else {
                Some(
                    scopes
                        .iter()
                        .map(|scope| scope.name.as_str())
                        .collect::<Vec<_>>()
                        .join("."),
                )
            },
        });
    }
}

fn next_kind(tokens: &[Token], mut at: usize) -> Option<u16> {
    while at < tokens.len()
        && matches!(
            tokens[at].kind,
            kind::WHITESPACE | kind::COMMENT | kind::NEWLINE
        )
    {
        at += 1;
    }
    tokens.get(at).map(|token| token.kind)
}

fn next_real(tokens: &[Token], mut at: usize, in_brackets: bool) -> Option<usize> {
    while at < tokens.len()
        && (matches!(tokens[at].kind, kind::WHITESPACE | kind::COMMENT)
            || (in_brackets && tokens[at].kind == kind::NEWLINE))
    {
        at += 1;
    }
    (at < tokens.len()).then_some(at)
}

/// `{name!r}` (a conversion) and `{value:.1f}` (a format type) are not
/// names, as in the walk (`Cursor::format_spec_letter`).
fn conversion_or_format<'s>(
    tokens: &[Token],
    before: Option<usize>,
    next: Option<usize>,
    name: &str,
    text: &impl Fn(&Token) -> &'s str,
) -> bool {
    let mut chars = name.chars();
    let (Some(ch), None) = (chars.next(), chars.next()) else {
        return false;
    };
    let Some(before) = before.map(|index| &tokens[index]) else {
        return false;
    };
    if before.kind == kind::OP && text(before) == "!" && matches!(ch, 'r' | 's' | 'a') {
        return true;
    }
    let closes =
        next.is_some_and(|index| tokens[index].kind == kind::OP && text(&tokens[index]) == "}");
    if !closes || !"sSdDxXoObBeEfFgGn%".contains(ch) {
        return false;
    }
    before.kind == kind::NUMBER
        || (before.kind == kind::OP
            && matches!(text(before), "." | "<" | ">" | "^" | "=" | "#" | ":" | "}"))
}
