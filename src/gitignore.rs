//! Gitignore matcher. Same rules as the Python 1.5.2 matcher: one file at
//! the root, last match wins, `**`, `*`, `?`, classes, and directory-only
//! patterns. No regular-expression crate.

use std::fs;
use std::path::Path;

#[derive(Clone, Debug)]
enum Part {
    Lit(Vec<u8>),
    Star,
    Any,
    OptSlashAny,
    One,
    Class { negated: bool, bytes: Vec<u8> },
}

#[derive(Clone, Debug)]
struct Rule {
    parts: Vec<Part>,
    anchored: bool,
    negate: bool,
    dir_only: bool,
}

#[derive(Clone, Debug)]
pub struct Gitignore {
    rules: Vec<Rule>,
}

impl Gitignore {
    pub fn parse(text: &str) -> Option<Gitignore> {
        let mut rules = Vec::new();
        for raw in text.lines() {
            let line = raw.trim_end();
            if line.is_empty() || line.trim_start().starts_with('#') {
                continue;
            }
            let (negate, line) = match line.strip_prefix('!') {
                Some(rest) => (true, rest),
                None => (false, line),
            };
            let (dir_only, line) = if let Some(rest) = line.strip_suffix('/') {
                (true, rest)
            } else {
                (false, line)
            };
            if line.is_empty() {
                continue;
            }
            let anchored = line.starts_with('/') || line.contains('/');
            let body = line.trim_start_matches('/');
            rules.push(Rule {
                parts: translate(body),
                anchored,
                negate,
                dir_only,
            });
        }
        if rules.is_empty() {
            None
        } else {
            Some(Gitignore { rules })
        }
    }

    pub fn load(root: &Path) -> Option<Gitignore> {
        let text = fs::read_to_string(root.join(".gitignore")).ok()?;
        Gitignore::parse(&text)
    }

    pub fn is_ignored(&self, rel_path: &str, is_dir: bool) -> bool {
        let rel = rel_path.replace('\\', "/");
        let parts: Vec<&str> = rel.split('/').filter(|part| !part.is_empty()).collect();
        for index in 1..parts.len() {
            let prefix = parts[..index].join("/");
            if self.last_match(&prefix, true) == Some(true) {
                return true;
            }
        }
        self.last_match(&rel, is_dir) == Some(true)
    }

    fn last_match(&self, rel: &str, is_dir: bool) -> Option<bool> {
        let mut verdict = None;
        for rule in &self.rules {
            if rule.dir_only && !is_dir {
                continue;
            }
            if rule_matches(rule, rel) {
                verdict = Some(!rule.negate);
            }
        }
        verdict
    }
}

fn rule_matches(rule: &Rule, rel: &str) -> bool {
    let bytes = rel.as_bytes();
    if rule.anchored {
        return parts_match(&rule.parts, bytes);
    }
    if parts_match(&rule.parts, bytes) {
        return true;
    }
    for (index, byte) in bytes.iter().enumerate() {
        if *byte == b'/' && parts_match(&rule.parts, &bytes[index + 1..]) {
            return true;
        }
    }
    false
}

fn parts_match(parts: &[Part], text: &[u8]) -> bool {
    fn rec(parts: &[Part], text: &[u8]) -> bool {
        let Some(part) = parts.first() else {
            return text.is_empty();
        };
        match part {
            Part::Lit(literal) => {
                text.starts_with(literal) && rec(&parts[1..], &text[literal.len()..])
            }
            Part::Star => {
                let mut index = 0;
                loop {
                    if rec(&parts[1..], &text[index..]) {
                        return true;
                    }
                    if index >= text.len() || text[index] == b'/' {
                        return false;
                    }
                    index += 1;
                }
            }
            Part::Any => {
                let mut index = 0;
                loop {
                    if rec(&parts[1..], &text[index..]) {
                        return true;
                    }
                    if index >= text.len() {
                        return false;
                    }
                    index += 1;
                }
            }
            Part::OptSlashAny => {
                if rec(&parts[1..], text) {
                    return true;
                }
                for (index, byte) in text.iter().enumerate() {
                    if *byte == b'/' && rec(&parts[1..], &text[index + 1..]) {
                        return true;
                    }
                }
                false
            }
            Part::One => !text.is_empty() && text[0] != b'/' && rec(&parts[1..], &text[1..]),
            Part::Class { negated, bytes } => {
                if text.is_empty() || text[0] == b'/' {
                    return false;
                }
                let inside = class_contains(bytes, text[0]);
                if inside == *negated {
                    return false;
                }
                rec(&parts[1..], &text[1..])
            }
        }
    }
    rec(parts, text)
}

fn class_contains(spec: &[u8], byte: u8) -> bool {
    let mut index = 0;
    while index < spec.len() {
        if index + 2 < spec.len() && spec[index + 1] == b'-' {
            if byte >= spec[index] && byte <= spec[index + 2] {
                return true;
            }
            index += 3;
        } else {
            if spec[index] == byte {
                return true;
            }
            index += 1;
        }
    }
    false
}

fn translate(pattern: &str) -> Vec<Part> {
    let bytes = pattern.as_bytes();
    let mut parts = Vec::new();
    let mut literal = Vec::new();
    let mut index = 0;
    let flush = |literal: &mut Vec<u8>, parts: &mut Vec<Part>| {
        if !literal.is_empty() {
            parts.push(Part::Lit(std::mem::take(literal)));
        }
    };
    while index < bytes.len() {
        match bytes[index] {
            b'*' if bytes.get(index..index + 3) == Some(b"**/") => {
                flush(&mut literal, &mut parts);
                parts.push(Part::OptSlashAny);
                index += 3;
            }
            b'*' if bytes.get(index..index + 2) == Some(b"**") => {
                flush(&mut literal, &mut parts);
                parts.push(Part::Any);
                index += 2;
            }
            b'*' => {
                flush(&mut literal, &mut parts);
                parts.push(Part::Star);
                index += 1;
            }
            b'?' => {
                flush(&mut literal, &mut parts);
                parts.push(Part::One);
                index += 1;
            }
            b'[' => {
                if let Some((spec, negated, next)) = parse_class(bytes, index) {
                    flush(&mut literal, &mut parts);
                    parts.push(Part::Class {
                        negated,
                        bytes: spec,
                    });
                    index = next;
                } else {
                    literal.push(b'[');
                    index += 1;
                }
            }
            byte => {
                literal.push(byte);
                index += 1;
            }
        }
    }
    flush(&mut literal, &mut parts);
    parts
}

fn parse_class(bytes: &[u8], start: usize) -> Option<(Vec<u8>, bool, usize)> {
    let mut index = start + 1;
    let mut negated = false;
    if matches!(bytes.get(index), Some(b'!' | b'^')) {
        negated = true;
        index += 1;
    }
    if bytes.get(index) == Some(&b']') {
        index += 1;
    }
    let body_start = index;
    while index < bytes.len() && bytes[index] != b']' {
        index += 1;
    }
    if index >= bytes.len() {
        return None;
    }
    Some((bytes[body_start..index].to_vec(), negated, index + 1))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ignored(lines: &str, path: &str) -> bool {
        Gitignore::parse(lines).unwrap().is_ignored(path, false)
    }

    #[test]
    fn basename_anchored_negation_and_globs() {
        assert!(ignored("*.log", "debug.log"));
        assert!(ignored("*.log", "deep/nested/debug.log"));
        assert!(!ignored("*.log", "debug.txt"));
        assert!(ignored("/build.py", "build.py"));
        assert!(!ignored("/build.py", "src/build.py"));
        assert!(ignored("docs/internal.md", "docs/internal.md"));
        assert!(!ignored("docs/internal.md", "other/docs/internal.md"));
        assert!(ignored("generated/", "generated/out.py"));
        assert!(ignored("generated/", "sub/generated/out.py"));
        assert!(!Gitignore::parse("generated/")
            .unwrap()
            .is_ignored("generated", false));
        assert!(ignored("*.log\n!keep.log", "other.log"));
        assert!(!ignored("*.log\n!keep.log", "keep.log"));
        assert!(ignored("src/**/temp.py", "src/a/b/temp.py"));
        assert!(ignored("src/**/temp.py", "src/temp.py"));
        assert!(!ignored("src/**/temp.py", "lib/a/temp.py"));
        assert!(ignored("cache?.py", "cache1.py"));
        assert!(!ignored("cache?.py", "cache12.py"));
        assert!(ignored("v[0-9].txt", "v3.txt"));
        assert!(!ignored("v[0-9].txt", "vx.txt"));
        assert!(ignored("# comment\n\n*.tmp", "a.tmp"));
        assert!(ignored("vendor", "vendor/lib/code.py"));
    }
}
