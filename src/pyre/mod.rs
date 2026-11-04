//! Python-compatible regular expressions for the patterns Stele already uses.
//!
//! The matcher is leftmost-first with greedy quantifiers, Unicode `\w` / `\s`,
//! and the lookaround those patterns contain. User-facing search rejects
//! lookaround and backreferences with a structured error; chunk and symbol
//! patterns are trusted and may use the features they were written with.

#[derive(Clone, Debug)]
enum Node {
    Empty,
    Lit(char),
    Any,
    Class {
        negated: bool,
        parts: Vec<ClassPart>,
    },
    Word,
    NotWord,
    Space,
    NotSpace,
    Digit,
    NotDigit,
    Bound,
    NotBound,
    Start,
    End,
    Concat(Vec<Node>),
    Alt(Vec<Node>),
    Repeat {
        inner: Box<Node>,
        min: u32,
        max: Option<u32>,
    },
    Group {
        index: usize,
        inner: Box<Node>,
    },
    Look {
        behind: bool,
        negative: bool,
        inner: Box<Node>,
    },
}

#[derive(Clone, Debug)]
enum ClassPart {
    Char(char),
    Range(char, char),
    Word,
    Space,
    Digit,
}

#[derive(Clone, Debug)]
pub struct Regex {
    root: Node,
    groups: usize,
    lookaround: bool,
    backref: bool,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Match {
    pub start: usize,
    pub end: usize,
    pub groups: Vec<Option<(usize, usize)>>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct RegexError {
    pub code: &'static str,
    pub message: String,
}

impl Regex {
    pub fn compile(pattern: &str) -> Result<Regex, RegexError> {
        let mut parser = Parser {
            chars: pattern.chars().collect(),
            index: 0,
            groups: 0,
            lookaround: false,
            backref: false,
        };
        let root = parser.alt()?;
        if parser.index != parser.chars.len() {
            return Err(parser.error("unparsed trailing pattern"));
        }
        Ok(Regex {
            root,
            groups: parser.groups,
            lookaround: parser.lookaround,
            backref: parser.backref,
        })
    }

    /// Linear-time subset. Lookaround and backreferences are rejected.
    pub fn compile_linear(pattern: &str) -> Result<Regex, RegexError> {
        let regex = Regex::compile(pattern)?;
        if regex.backref {
            return Err(RegexError {
                code: "regex_backreference",
                message: "backreferences are not supported".to_string(),
            });
        }
        if regex.lookaround {
            return Err(RegexError {
                code: "regex_lookaround",
                message: "lookahead and lookbehind are not supported".to_string(),
            });
        }
        Ok(regex)
    }

    pub fn find_iter(&self, text: &str) -> Vec<Match> {
        self.find_iter_bt(text)
    }

    pub fn search(&self, text: &str) -> Option<Match> {
        self.find_iter(text).into_iter().next()
    }
}

struct Parser {
    chars: Vec<char>,
    index: usize,
    groups: usize,
    lookaround: bool,
    backref: bool,
}

impl Parser {
    fn error(&self, message: &str) -> RegexError {
        RegexError {
            code: "regex_syntax",
            message: format!("{message} at {}", self.index),
        }
    }

    fn peek(&self) -> Option<char> {
        self.chars.get(self.index).copied()
    }

    fn bump(&mut self) -> Option<char> {
        let ch = self.peek()?;
        self.index += 1;
        Some(ch)
    }

    fn alt(&mut self) -> Result<Node, RegexError> {
        let mut arms = vec![self.concat()?];
        while self.peek() == Some('|') {
            self.bump();
            arms.push(self.concat()?);
        }
        Ok(if arms.len() == 1 {
            arms.pop().unwrap()
        } else {
            Node::Alt(arms)
        })
    }

    fn concat(&mut self) -> Result<Node, RegexError> {
        let mut parts = Vec::new();
        while let Some(ch) = self.peek() {
            if ch == '|' || ch == ')' {
                break;
            }
            parts.push(self.piece()?);
        }
        Ok(if parts.is_empty() {
            Node::Empty
        } else if parts.len() == 1 {
            parts.pop().unwrap()
        } else {
            Node::Concat(parts)
        })
    }

    fn piece(&mut self) -> Result<Node, RegexError> {
        let atom = self.atom()?;
        let (min, max) = match self.peek() {
            Some('*') => {
                self.bump();
                (0, None)
            }
            Some('+') => {
                self.bump();
                (1, None)
            }
            Some('?') => {
                self.bump();
                (0, Some(1))
            }
            Some('{') => self.brace()?,
            _ => return Ok(atom),
        };
        if self.peek() == Some('?') {
            self.bump();
        }
        Ok(Node::Repeat {
            inner: Box::new(atom),
            min,
            max,
        })
    }

    fn brace(&mut self) -> Result<(u32, Option<u32>), RegexError> {
        self.bump();
        let min = self.number()?;
        let max = if self.peek() == Some(',') {
            self.bump();
            if self.peek() == Some('}') {
                None
            } else {
                Some(self.number()?)
            }
        } else {
            Some(min)
        };
        if self.bump() != Some('}') {
            return Err(self.error("expected '}'"));
        }
        Ok((min, max))
    }

    fn number(&mut self) -> Result<u32, RegexError> {
        let start = self.index;
        while matches!(self.peek(), Some('0'..='9')) {
            self.bump();
        }
        if start == self.index {
            return Err(self.error("expected a number"));
        }
        let text: String = self.chars[start..self.index].iter().collect();
        text.parse::<u32>()
            .map_err(|_| self.error("number out of range"))
    }

    fn atom(&mut self) -> Result<Node, RegexError> {
        match self.bump() {
            Some('.') => Ok(Node::Any),
            Some('^') => Ok(Node::Start),
            Some('$') => Ok(Node::End),
            Some('[') => self.class(),
            Some('(') => self.group(),
            Some('\\') => self.escape(false),
            Some(ch) if ")*+?|{}".contains(ch) => Err(self.error("unexpected metacharacter")),
            Some(ch) => Ok(Node::Lit(ch)),
            None => Err(self.error("unexpected end")),
        }
    }

    fn group(&mut self) -> Result<Node, RegexError> {
        if self.peek() == Some('?') {
            self.bump();
            return self.extension();
        }
        self.groups += 1;
        let index = self.groups;
        let inner = self.alt()?;
        if self.bump() != Some(')') {
            return Err(self.error("unclosed group"));
        }
        Ok(Node::Group {
            index,
            inner: Box::new(inner),
        })
    }

    fn extension(&mut self) -> Result<Node, RegexError> {
        match self.bump() {
            Some(':') => {
                let inner = self.alt()?;
                if self.bump() != Some(')') {
                    return Err(self.error("unclosed group"));
                }
                Ok(inner)
            }
            Some('=') => self.look(false, false),
            Some('!') => self.look(false, true),
            Some('<') => match self.bump() {
                Some('=') => self.look(true, false),
                Some('!') => self.look(true, true),
                _ => Err(self.error("unsupported group extension")),
            },
            Some(ch) => {
                self.backref = true;
                Err(RegexError {
                    code: "regex_syntax",
                    message: format!("unsupported group extension '?{ch}'"),
                })
            }
            None => Err(self.error("unclosed group")),
        }
    }

    fn look(&mut self, behind: bool, negative: bool) -> Result<Node, RegexError> {
        self.lookaround = true;
        let inner = self.alt()?;
        if self.bump() != Some(')') {
            return Err(self.error("unclosed lookaround"));
        }
        Ok(Node::Look {
            behind,
            negative,
            inner: Box::new(inner),
        })
    }

    fn class(&mut self) -> Result<Node, RegexError> {
        let negated = self.peek() == Some('^');
        if negated {
            self.bump();
        }
        let mut parts = Vec::new();
        while self.peek().is_some() && self.peek() != Some(']') {
            if self.peek() == Some('\\') {
                self.bump();
                match self.bump() {
                    Some('w') => parts.push(ClassPart::Word),
                    Some('d') => parts.push(ClassPart::Digit),
                    Some('s') => parts.push(ClassPart::Space),
                    Some('n') => parts.push(ClassPart::Char('\n')),
                    Some('t') => parts.push(ClassPart::Char('\t')),
                    Some('r') => parts.push(ClassPart::Char('\r')),
                    Some(ch) => parts.push(ClassPart::Char(ch)),
                    None => return Err(self.error("bad class escape")),
                }
            } else {
                let ch = self.bump().unwrap();
                if self.peek() == Some('-') && self.chars.get(self.index + 1).copied() != Some(']')
                {
                    self.bump();
                    let end = self.bump().ok_or_else(|| self.error("bad range"))?;
                    parts.push(ClassPart::Range(ch, end));
                } else {
                    parts.push(ClassPart::Char(ch));
                }
            }
        }
        if self.bump() != Some(']') {
            return Err(self.error("unclosed class"));
        }
        Ok(Node::Class { negated, parts })
    }

    fn escape(&mut self, _in_class: bool) -> Result<Node, RegexError> {
        match self.bump() {
            Some('n') => Ok(Node::Lit('\n')),
            Some('t') => Ok(Node::Lit('\t')),
            Some('r') => Ok(Node::Lit('\r')),
            Some('w') => Ok(Node::Word),
            Some('W') => Ok(Node::NotWord),
            Some('s') => Ok(Node::Space),
            Some('S') => Ok(Node::NotSpace),
            Some('d') => Ok(Node::Digit),
            Some('D') => Ok(Node::NotDigit),
            Some('b') => Ok(Node::Bound),
            Some('B') => Ok(Node::NotBound),
            Some(ch) if ch.is_ascii_digit() => {
                self.backref = true;
                Err(RegexError {
                    code: "regex_backreference",
                    message: format!("backreference \\{ch} is not supported"),
                })
            }
            Some(ch) => Ok(Node::Lit(ch)),
            None => Err(self.error("trailing escape")),
        }
    }
}

fn atom_end(node: &Node, text: &[char], pos: usize) -> Option<usize> {
    match node {
        Node::Empty => Some(pos),
        Node::Lit(ch) => (text.get(pos) == Some(ch)).then_some(pos + 1),
        Node::Any => text.get(pos).filter(|ch| **ch != '\n').map(|_| pos + 1),
        Node::Class { negated, parts } => {
            let ch = *text.get(pos)?;
            (class_hit(ch, parts) != *negated).then_some(pos + 1)
        }
        Node::Word => text.get(pos).filter(|ch| is_word(**ch)).map(|_| pos + 1),
        Node::NotWord => text.get(pos).filter(|ch| !is_word(**ch)).map(|_| pos + 1),
        Node::Space => text
            .get(pos)
            .filter(|ch| ch.is_whitespace())
            .map(|_| pos + 1),
        Node::NotSpace => text
            .get(pos)
            .filter(|ch| !ch.is_whitespace())
            .map(|_| pos + 1),
        Node::Digit => text
            .get(pos)
            .filter(|ch| ch.is_ascii_digit())
            .map(|_| pos + 1),
        Node::NotDigit => text
            .get(pos)
            .filter(|ch| !ch.is_ascii_digit())
            .map(|_| pos + 1),
        Node::Bound => is_boundary(text, pos).then_some(pos),
        Node::NotBound => (!is_boundary(text, pos)).then_some(pos),
        Node::Start => (pos == 0).then_some(pos),
        Node::End => {
            (pos == text.len() || (pos + 1 == text.len() && text[pos] == '\n')).then_some(pos)
        }
        _ => None,
    }
}

fn class_hit(ch: char, parts: &[ClassPart]) -> bool {
    parts.iter().any(|part| match part {
        ClassPart::Char(expected) => ch == *expected,
        ClassPart::Range(lo, hi) => ch >= *lo && ch <= *hi,
        ClassPart::Word => is_word(ch),
        ClassPart::Space => ch.is_whitespace(),
        ClassPart::Digit => ch.is_ascii_digit(),
    })
}

fn is_word(ch: char) -> bool {
    ch == '_' || ch.is_alphanumeric()
}

fn is_boundary(text: &[char], pos: usize) -> bool {
    let left = pos > 0 && is_word(text[pos - 1]);
    let right = pos < text.len() && is_word(text[pos]);
    left != right
}

/// Backtracking matcher. `repeat` above is greedy-only; real patterns need
/// the rest of a concatenation to reject a too-long quantifier. `find_at`
/// is the entry that threads that continuation.
pub fn find_at(regex: &Regex, text: &str, start: usize) -> Option<Match> {
    let chars: Vec<char> = text.chars().collect();
    if start > chars.len() {
        return None;
    }
    let mut caps = vec![None; regex.groups + 1];
    let end = eval_bt(&regex.root, &chars, start, &mut caps, &mut 400_000)?;
    caps[0] = Some((start, end));
    Some(Match {
        start,
        end,
        groups: caps,
    })
}

fn eval_bt(
    node: &Node,
    text: &[char],
    pos: usize,
    caps: &mut Vec<Option<(usize, usize)>>,
    fuel: &mut u32,
) -> Option<usize> {
    if *fuel == 0 {
        return None;
    }
    *fuel -= 1;
    match node {
        Node::Concat(parts) => eval_concat(parts, 0, text, pos, caps, fuel),
        Node::Alt(arms) => {
            for arm in arms {
                let saved = caps.clone();
                if let Some(end) = eval_bt(arm, text, pos, caps, fuel) {
                    return Some(end);
                }
                *caps = saved;
            }
            None
        }
        Node::Group { index, inner } => {
            let saved = caps.get(*index).copied().flatten();
            if let Some(slot) = caps.get_mut(*index) {
                *slot = Some((pos, pos));
            }
            match eval_bt(inner, text, pos, caps, fuel) {
                Some(end) => {
                    if let Some(slot) = caps.get_mut(*index) {
                        *slot = Some((pos, end));
                    }
                    Some(end)
                }
                None => {
                    if let Some(slot) = caps.get_mut(*index) {
                        *slot = saved;
                    }
                    None
                }
            }
        }
        Node::Look {
            behind,
            negative,
            inner,
        } => {
            let saved = caps.clone();
            let hit = if *behind {
                let start = pos.saturating_sub(64);
                (start..=pos).rev().any(|from| {
                    let mut trial = saved.clone();
                    eval_bt(inner, text, from, &mut trial, fuel) == Some(pos)
                })
            } else {
                eval_bt(inner, text, pos, caps, fuel).is_some()
            };
            if hit == *negative || *behind {
                *caps = saved;
            }
            (hit != *negative).then_some(pos)
        }
        Node::Repeat { inner, min, max } => {
            let mut ends = vec![pos];
            let mut at = pos;
            let mut snapshots = vec![caps.clone()];
            let limit = max.unwrap_or(10_000);
            while (ends.len() as u32 - 1) < limit {
                let mut trial = caps.clone();
                match eval_bt(inner, text, at, &mut trial, fuel) {
                    Some(next) if next > at => {
                        at = next;
                        ends.push(at);
                        snapshots.push(trial);
                    }
                    _ => break,
                }
            }
            let floor = *min as usize;
            if ends.len() <= floor {
                return None;
            }
            *caps = snapshots[ends.len() - 1].clone();
            Some(ends[ends.len() - 1])
        }
        other => atom_end(other, text, pos),
    }
}

fn eval_concat(
    parts: &[Node],
    index: usize,
    text: &[char],
    pos: usize,
    caps: &mut Vec<Option<(usize, usize)>>,
    fuel: &mut u32,
) -> Option<usize> {
    if index == parts.len() {
        return Some(pos);
    }
    match &parts[index] {
        Node::Repeat { inner, min, max } => {
            let mut ends = vec![pos];
            let mut at = pos;
            let mut snapshots = vec![caps.clone()];
            let limit = max.unwrap_or(10_000);
            while (ends.len() as u32 - 1) < limit && *fuel > 0 {
                let mut trial = caps.clone();
                match eval_bt(inner, text, at, &mut trial, fuel) {
                    Some(next) if next > at => {
                        at = next;
                        ends.push(at);
                        snapshots.push(trial);
                    }
                    _ => break,
                }
            }
            let floor = *min as usize;
            if ends.len() <= floor {
                return None;
            }
            for choice in (floor..ends.len()).rev() {
                let saved = caps.clone();
                *caps = snapshots[choice].clone();
                if let Some(end) = eval_concat(parts, index + 1, text, ends[choice], caps, fuel) {
                    return Some(end);
                }
                *caps = saved;
            }
            None
        }
        _ => {
            let saved = caps.clone();
            let next = eval_bt(&parts[index], text, pos, caps, fuel)?;
            match eval_concat(parts, index + 1, text, next, caps, fuel) {
                Some(end) => Some(end),
                None => {
                    *caps = saved;
                    None
                }
            }
        }
    }
}

impl Regex {
    pub fn find_iter_bt(&self, text: &str) -> Vec<Match> {
        let chars: Vec<char> = text.chars().collect();
        let mut out = Vec::new();
        let mut pos = 0usize;
        while pos <= chars.len() {
            if let Some(found) = find_at_chars(self, &chars, pos) {
                let end = found.end;
                out.push(found);
                pos = if end == pos { pos + 1 } else { end };
            } else {
                pos += 1;
            }
        }
        out
    }
}

fn find_at_chars(regex: &Regex, chars: &[char], start: usize) -> Option<Match> {
    let mut caps = vec![None; regex.groups + 1];
    let end = eval_bt(&regex.root, chars, start, &mut caps, &mut 400_000)?;
    caps[0] = Some((start, end));
    Some(Match {
        start,
        end,
        groups: caps,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn alternation_and_anchors_match_python_finditer() {
        let regex = Regex::compile(r"(?:^|\n)(?:pub\s+)?(?:async\s+)?fn\s+\w+").unwrap();
        let text = "fn alpha() {}\npub async fn beta() {}";
        let hits = regex.find_iter_bt(text);
        assert_eq!(hits.len(), 2);
        assert_eq!(hits[0].start, 0);
        assert_eq!(&text[hits[1].start..hits[1].end], "\npub async fn beta");
    }

    #[test]
    fn negative_lookahead_skips_return() {
        let regex = Regex::compile(r"(?!(?:return|if)[ \t])[A-Za-z_]\w*").unwrap();
        let hits = regex.find_iter_bt("return Compute");
        let words: Vec<_> = hits
            .iter()
            .map(|hit| "return Compute"[hit.start..hit.end].to_string())
            .collect();
        assert!(words.contains(&"Compute".to_string()));
        assert!(!words.contains(&"return".to_string()));
    }

    #[test]
    fn linear_compile_rejects_lookaround() {
        let error = Regex::compile_linear(r"(?!x)y").unwrap_err();
        assert_eq!(error.code, "regex_lookaround");
    }
}
