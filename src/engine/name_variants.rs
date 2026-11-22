//! `named_in.variants` (2.6): the spellings code derives from a CamelCase
//! name for dynamic dispatch. sqlglot finds a generator method by
//! `f"{node.key}_sql"` (`key` the lowercased class name) and spells SQL
//! names from the class name, so renaming `StrPosition` must also touch
//! `def strposition_sql` and `"STR_POSITION"`. Neither is the symbol's
//! name, so no reference row and no word match finds them.
//!
//! The forms are the lowercase joined name (`strposition`), snake_case
//! (`str_position`) and UPPER_SNAKE (`STR_POSITION`). One matches as a
//! whole word or as one `_`-separated part of an identifier
//! (`strposition_sql`, `_strposition_sql`, `build_locate_strposition`), in
//! every indexed file, code, strings and comments alike. A form shorter
//! than `VARIANT_MIN_CHARS` or one that is a common English word is not
//! scanned.

use super::Engine;
use crate::json::Value;
use std::collections::{BTreeMap, BTreeSet, HashMap};

/// Forms shorter than this are not scanned (`to_date` would match noise).
const VARIANT_MIN_CHARS: usize = 6;
/// Variant hits listed; the rest are counted.
const VARIANT_HITS: usize = 20;

/// Lowercase joined forms that are ordinary words (`TimeStamp` gives
/// `timestamp`), too common in code and prose to mean the symbol.
const COMMON_WORDS: &[&str] = &[
    "another",
    "because",
    "between",
    "checkbox",
    "database",
    "dataset",
    "download",
    "everyone",
    "everything",
    "feedback",
    "filename",
    "however",
    "keyboard",
    "keyword",
    "layout",
    "lookup",
    "network",
    "nothing",
    "notebook",
    "offline",
    "online",
    "otherwise",
    "outside",
    "output",
    "overflow",
    "override",
    "password",
    "pathname",
    "playback",
    "readonly",
    "someone",
    "something",
    "sometimes",
    "textbox",
    "timeout",
    "timestamp",
    "together",
    "typename",
    "underline",
    "update",
    "upload",
    "username",
    "whatever",
    "within",
    "without",
    "workflow",
    "workspace",
];

/// The words of a CamelCase name: `StrPosition` gives `Str`, `Position`;
/// `HTTPServer` gives `HTTP`, `Server`; a digit stays with the word before
/// it. A name with `_`, or one that is not all letters and digits, has
/// none.
fn camel_words(name: &str) -> Vec<String> {
    let chars: Vec<char> = name.chars().collect();
    if chars.is_empty()
        || !chars[0].is_ascii_alphabetic()
        || !chars.iter().all(|ch| ch.is_ascii_alphanumeric())
    {
        return Vec::new();
    }
    let mut words: Vec<String> = Vec::new();
    let mut current = String::new();
    for (at, &ch) in chars.iter().enumerate() {
        let prev = at.checked_sub(1).map(|index| chars[index]);
        let next = chars.get(at + 1).copied();
        let boundary = ch.is_ascii_uppercase()
            && prev.is_some_and(|prev| {
                prev.is_ascii_lowercase()
                    || prev.is_ascii_digit()
                    || (prev.is_ascii_uppercase() && next.is_some_and(|n| n.is_ascii_lowercase()))
            });
        if boundary && !current.is_empty() {
            words.push(std::mem::take(&mut current));
        }
        current.push(ch);
    }
    if !current.is_empty() {
        words.push(current);
    }
    words
}

/// The dispatch forms of `name` worth scanning, in a fixed order
/// (lowercase joined, snake_case, UPPER_SNAKE). Only a name of two or more
/// CamelCase words has any.
pub(super) fn variants(name: &str) -> Vec<String> {
    let words = camel_words(name);
    if words.len() < 2 {
        return Vec::new();
    }
    let lower: Vec<String> = words.iter().map(|word| word.to_ascii_lowercase()).collect();
    let joined = lower.concat();
    let snake = lower.join("_");
    let upper = snake.to_ascii_uppercase();
    let mut out: Vec<String> = Vec::new();
    for (form, word_check) in [(joined, true), (snake, false), (upper, false)] {
        if form == name || out.contains(&form) || form.chars().count() < VARIANT_MIN_CHARS {
            continue;
        }
        if word_check && COMMON_WORDS.contains(&form.as_str()) {
            continue;
        }
        out.push(form);
    }
    out
}

/// The 1-based lines of `text` where `needle` stands as a word or as one
/// `_`-separated part of an identifier: on each side the text ends, or a
/// character that is not a letter or digit sits there (`_` counts as a
/// separator).
fn part_lines(text: &str, needle: &str) -> BTreeSet<u32> {
    let mut out = BTreeSet::new();
    let bytes = text.as_bytes();
    let separator = |byte: Option<&u8>| byte.is_none_or(|byte| !byte.is_ascii_alphanumeric());
    for (at, _) in text.match_indices(needle) {
        let end = at + needle.len();
        let before = at.checked_sub(1).and_then(|index| bytes.get(index));
        // A non-ASCII letter next to it is part of a longer word.
        let before_ok = separator(before) && before.is_none_or(|byte| byte.is_ascii());
        let after = bytes.get(end);
        let after_ok = separator(after) && after.is_none_or(|byte| byte.is_ascii());
        if before_ok && after_ok {
            let line = text[..at].bytes().filter(|byte| *byte == b'\n').count() as u32 + 1;
            out.insert(line);
        }
    }
    out
}

impl Engine {
    /// The `variants` block of `named_in` for `name`, or None when `name`
    /// has no form worth scanning. Each hit says the form it matched
    /// (`variant`; `also` for more forms on that line) and, when the line
    /// defines an identifier holding one (`def strposition_sql`), that
    /// identifier: those are the dispatch targets and are listed first.
    /// `VARIANT_HITS` are listed unless `all`.
    pub(super) fn name_variants(&self, name: &str, all: bool) -> Option<Value> {
        let forms = variants(name);
        if forms.is_empty() {
            return None;
        }
        // (path, line) -> the forms on that line, in `forms` order.
        let mut hits: BTreeMap<(String, i64), Vec<&str>> = BTreeMap::new();
        for chunk in &self.store.state.chunks {
            for form in &forms {
                if !chunk.content.contains(form.as_str()) {
                    continue;
                }
                for line in part_lines(&chunk.content, form) {
                    let file_line = crate::chunk::lines::absolute(&chunk.line_map, line) as i64;
                    let found = hits.entry((chunk.path.clone(), file_line)).or_default();
                    if !found.contains(&form.as_str()) {
                        found.push(form.as_str());
                    }
                }
            }
        }
        for found in hits.values_mut() {
            found.sort_by_key(|form| forms.iter().position(|one| one == form));
        }
        let mut defines: HashMap<(String, i64), String> = HashMap::new();
        if !hits.is_empty() {
            for sym in &self.store.state.symbols {
                if sym.role != "definition" {
                    continue;
                }
                let key = (sym.path.clone(), sym.line as i64);
                if let Some(found) = hits.get(&key) {
                    if found
                        .iter()
                        .any(|form| !part_lines(&sym.name, form).is_empty())
                    {
                        defines.entry(key).or_insert_with(|| sym.name.clone());
                    }
                }
            }
        }
        let lines_total = hits.len();
        let files_total = hits
            .keys()
            .map(|(path, _)| path.as_str())
            .collect::<BTreeSet<_>>()
            .len();
        // Definitions first, then the first line of each form no listed
        // line has yet (a rare `STR_POSITION` is not buried under
        // `strposition`), then one line per file before a file's second, so
        // the listed hits cover as many files as they can.
        let mut by_form: BTreeMap<&str, i64> = BTreeMap::new();
        let mut shown: BTreeSet<&str> = BTreeSet::new();
        for (key, found) in &hits {
            for form in found {
                *by_form.entry(form).or_insert(0) += 1;
            }
            if defines.contains_key(key) {
                shown.extend(found.iter().copied());
            }
        }
        let mut order: Vec<(u8, usize, &(String, i64))> = Vec::new();
        let mut per_file: HashMap<&str, usize> = HashMap::new();
        for (key, found) in &hits {
            let rank = per_file.entry(key.0.as_str()).or_insert(0);
            let tier = if defines.contains_key(key) {
                0
            } else if found.iter().any(|form| !shown.contains(form)) {
                shown.extend(found.iter().copied());
                1
            } else {
                2
            };
            order.push((tier, *rank, key));
            *rank += 1;
        }
        order.sort();
        let limit = if all { usize::MAX } else { VARIANT_HITS };
        let listed: Vec<Value> = order
            .into_iter()
            .take(limit)
            .map(|(_, _, key)| {
                let found = &hits[key];
                let mut row = vec![
                    ("path", Value::string(&key.0)),
                    ("line", Value::Int(key.1)),
                    ("variant", Value::string(found[0])),
                ];
                if found.len() > 1 {
                    row.push((
                        "also",
                        Value::Array(found[1..].iter().map(|form| Value::string(*form)).collect()),
                    ));
                }
                if let Some(defined) = defines.get(key) {
                    row.push(("defines", Value::string(defined)));
                }
                Value::object(row)
            })
            .collect();
        let mut out = vec![
            (
                "forms",
                Value::Array(forms.iter().map(Value::string).collect()),
            ),
            ("files_total", Value::Int(files_total as i64)),
            ("lines_total", Value::Int(lines_total as i64)),
            (
                "by_variant",
                Value::Object(
                    forms
                        .iter()
                        .filter_map(|form| {
                            by_form
                                .get(form.as_str())
                                .map(|count| (form.clone(), Value::Int(*count)))
                        })
                        .collect(),
                ),
            ),
            ("hits", Value::Array(listed)),
        ];
        if lines_total > limit {
            out.push(("omitted", Value::Int((lines_total - limit) as i64)));
            out.push((
                "hint",
                Value::string(
                    "full=true (CLI --limit 0) on find_references lists every variant line.",
                ),
            ));
        }
        if lines_total > 0 {
            out.push((
                "note",
                Value::string(format!(
                    "Lines spelling {name} as {}: code may reach the symbol through a name built from it (`f\"{{key}}_sql\"`, a SQL name). A rename usually changes these too; `defines` marks a definition.",
                    forms.join(", ")
                )),
            ));
        }
        Some(Value::object(out))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn camel_case_names_give_their_dispatch_forms() {
        assert_eq!(
            variants("StrPosition"),
            vec!["strposition", "str_position", "STR_POSITION"]
        );
        assert_eq!(
            variants("HTTPServerError"),
            vec!["httpservererror", "http_server_error", "HTTP_SERVER_ERROR"]
        );
        assert_eq!(
            variants("Sha256Hash"),
            vec!["sha256hash", "sha256_hash", "SHA256_HASH"]
        );
        // One word, a snake name, or forms too short: nothing to scan.
        assert!(variants("Column").is_empty());
        assert!(variants("str_position").is_empty());
        assert!(variants("ToDo").is_empty());
        // `TimeStamp` is the word `timestamp`; its snake forms still count.
        assert_eq!(variants("TimeStamp"), vec!["time_stamp", "TIME_STAMP"]);
    }

    #[test]
    fn a_form_matches_as_a_word_or_an_underscore_part() {
        let text = "def strposition_sql(x):\n    _strposition_sql(x)\n    STRPOSITIONS = 1\n    build_locate_strposition()\n    mystrposition = 2\n    \"STR_POSITION(a, b)\"\n";
        assert_eq!(
            part_lines(text, "strposition")
                .into_iter()
                .collect::<Vec<_>>(),
            vec![1, 2, 4]
        );
        assert_eq!(
            part_lines(text, "STR_POSITION")
                .into_iter()
                .collect::<Vec<_>>(),
            vec![6]
        );
    }
}
