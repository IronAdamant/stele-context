//! JSON parser and writer. Object keys keep insertion order.

use std::fmt::Write as _;

#[derive(Clone, Debug, PartialEq)]
pub enum Value {
    Null,
    Bool(bool),
    Int(i64),
    Float(f64),
    String(String),
    Array(Vec<Value>),
    Object(Vec<(String, Value)>),
}

impl Value {
    pub fn object(pairs: Vec<(&str, Value)>) -> Value {
        Value::Object(
            pairs
                .into_iter()
                .map(|(key, value)| (key.to_string(), value))
                .collect(),
        )
    }

    pub fn string(text: impl Into<String>) -> Value {
        Value::String(text.into())
    }
}

#[derive(Debug, PartialEq, Eq)]
pub struct ParseError {
    pub message: String,
    pub offset: usize,
}

pub fn parse(input: &str) -> Result<Value, ParseError> {
    let mut parser = Parser {
        bytes: input.as_bytes(),
        index: 0,
    };
    parser.skip();
    let value = parser.value()?;
    parser.skip();
    if parser.index != parser.bytes.len() {
        return Err(parser.error("trailing input"));
    }
    Ok(value)
}

/// `json.dumps` default separators and ASCII escapes, for zlib KV files.
pub fn dumps_python(value: &Value) -> String {
    let mut out = String::new();
    write_python(&mut out, value);
    out
}

fn write_python(out: &mut String, value: &Value) {
    match value {
        Value::Array(items) => {
            out.push('[');
            for (index, item) in items.iter().enumerate() {
                if index > 0 {
                    out.push_str(", ");
                }
                write_python(out, item);
            }
            out.push(']');
        }
        Value::Object(pairs) => {
            out.push('{');
            for (index, (key, item)) in pairs.iter().enumerate() {
                if index > 0 {
                    out.push_str(", ");
                }
                write_string(out, key);
                out.push_str(": ");
                write_python(out, item);
            }
            out.push('}');
        }
        other => write_value(out, other),
    }
}

pub fn to_string(value: &Value) -> String {
    let mut out = String::new();
    write_value(&mut out, value);
    out
}

pub fn to_vec(value: &Value) -> Vec<u8> {
    to_string(value).into_bytes()
}

struct Parser<'a> {
    bytes: &'a [u8],
    index: usize,
}

impl<'a> Parser<'a> {
    fn error(&self, message: &str) -> ParseError {
        ParseError {
            message: message.to_string(),
            offset: self.index,
        }
    }

    fn skip(&mut self) {
        while let Some(byte) = self.bytes.get(self.index) {
            if byte.is_ascii_whitespace() {
                self.index += 1;
            } else {
                break;
            }
        }
    }

    fn value(&mut self) -> Result<Value, ParseError> {
        self.skip();
        match self.bytes.get(self.index).copied() {
            Some(b'n') => self.literal(b"null", Value::Null),
            Some(b't') => self.literal(b"true", Value::Bool(true)),
            Some(b'f') => self.literal(b"false", Value::Bool(false)),
            Some(b'"') => Ok(Value::String(self.string()?)),
            Some(b'[') => self.array(),
            Some(b'{') => self.object(),
            Some(b'-') | Some(b'0'..=b'9') => self.number(),
            _ => Err(self.error("expected a JSON value")),
        }
    }

    fn literal(&mut self, text: &[u8], value: Value) -> Result<Value, ParseError> {
        if self.bytes[self.index..].starts_with(text) {
            self.index += text.len();
            Ok(value)
        } else {
            Err(self.error("invalid literal"))
        }
    }

    fn array(&mut self) -> Result<Value, ParseError> {
        self.index += 1;
        let mut items = Vec::new();
        self.skip();
        if self.bytes.get(self.index) == Some(&b']') {
            self.index += 1;
            return Ok(Value::Array(items));
        }
        loop {
            items.push(self.value()?);
            self.skip();
            match self.bytes.get(self.index) {
                Some(b',') => {
                    self.index += 1;
                    self.skip();
                }
                Some(b']') => {
                    self.index += 1;
                    break;
                }
                _ => return Err(self.error("expected ',' or ']'")),
            }
        }
        Ok(Value::Array(items))
    }

    fn object(&mut self) -> Result<Value, ParseError> {
        self.index += 1;
        let mut pairs = Vec::new();
        self.skip();
        if self.bytes.get(self.index) == Some(&b'}') {
            self.index += 1;
            return Ok(Value::Object(pairs));
        }
        loop {
            self.skip();
            if self.bytes.get(self.index) != Some(&b'"') {
                return Err(self.error("expected a string key"));
            }
            let key = self.string()?;
            self.skip();
            if self.bytes.get(self.index) != Some(&b':') {
                return Err(self.error("expected ':'"));
            }
            self.index += 1;
            let value = self.value()?;
            if let Some(existing) = pairs.iter_mut().find(|(name, _)| name == &key) {
                existing.1 = value;
            } else {
                pairs.push((key, value));
            }
            self.skip();
            match self.bytes.get(self.index) {
                Some(b',') => self.index += 1,
                Some(b'}') => {
                    self.index += 1;
                    break;
                }
                _ => return Err(self.error("expected ',' or '}'")),
            }
        }
        Ok(Value::Object(pairs))
    }

    fn string(&mut self) -> Result<String, ParseError> {
        self.index += 1;
        let mut out = Vec::new();
        loop {
            let rest = &self.bytes[self.index..];
            let special = rest
                .iter()
                .position(|byte| *byte == b'"' || *byte == b'\\' || *byte < 0x20);
            let Some(special) = special else {
                return Err(self.error("unterminated string"));
            };
            out.extend_from_slice(&rest[..special]);
            self.index += special;
            match self.bytes[self.index] {
                b'"' => {
                    self.index += 1;
                    return String::from_utf8(out).map_err(|_| self.error("invalid utf-8"));
                }
                b'\\' => {
                    self.index += 1;
                    let esc = self
                        .bytes
                        .get(self.index)
                        .copied()
                        .ok_or_else(|| self.error("truncated escape"))?;
                    self.index += 1;
                    match esc {
                        b'"' | b'\\' | b'/' => out.push(esc),
                        b'b' => out.push(0x08),
                        b'f' => out.push(0x0c),
                        b'n' => out.push(b'\n'),
                        b'r' => out.push(b'\r'),
                        b't' => out.push(b'\t'),
                        b'u' => {
                            let ch = self.unicode_escape()?;
                            let mut buf = [0u8; 4];
                            out.extend_from_slice(ch.encode_utf8(&mut buf).as_bytes());
                        }
                        _ => return Err(self.error("invalid escape")),
                    }
                }
                _ => return Err(self.error("unescaped control character")),
            }
        }
    }

    fn unicode_escape(&mut self) -> Result<char, ParseError> {
        if self.index + 4 > self.bytes.len() {
            return Err(self.error("truncated unicode escape"));
        }
        let hex = std::str::from_utf8(&self.bytes[self.index..self.index + 4])
            .map_err(|_| self.error("invalid unicode escape"))?;
        self.index += 4;
        let code =
            u32::from_str_radix(hex, 16).map_err(|_| self.error("invalid unicode escape"))?;
        char::from_u32(code).ok_or_else(|| self.error("invalid unicode scalar"))
    }

    fn number(&mut self) -> Result<Value, ParseError> {
        let start = self.index;
        if self.bytes.get(self.index) == Some(&b'-') {
            self.index += 1;
        }
        match self.bytes.get(self.index) {
            Some(b'0') => self.index += 1,
            Some(b'1'..=b'9') => {
                self.index += 1;
                while matches!(self.bytes.get(self.index), Some(b'0'..=b'9')) {
                    self.index += 1;
                }
            }
            _ => return Err(self.error("invalid number")),
        }
        let mut fractional = false;
        if self.bytes.get(self.index) == Some(&b'.') {
            fractional = true;
            self.index += 1;
            if !matches!(self.bytes.get(self.index), Some(b'0'..=b'9')) {
                return Err(self.error("invalid fraction"));
            }
            while matches!(self.bytes.get(self.index), Some(b'0'..=b'9')) {
                self.index += 1;
            }
        }
        if matches!(self.bytes.get(self.index), Some(b'e' | b'E')) {
            fractional = true;
            self.index += 1;
            if matches!(self.bytes.get(self.index), Some(b'+' | b'-')) {
                self.index += 1;
            }
            if !matches!(self.bytes.get(self.index), Some(b'0'..=b'9')) {
                return Err(self.error("invalid exponent"));
            }
            while matches!(self.bytes.get(self.index), Some(b'0'..=b'9')) {
                self.index += 1;
            }
        }
        let lexeme = std::str::from_utf8(&self.bytes[start..self.index])
            .map_err(|_| self.error("invalid number"))?;
        if !fractional {
            if let Ok(integer) = lexeme.parse::<i64>() {
                return Ok(Value::Int(integer));
            }
        }
        let float = lexeme
            .parse::<f64>()
            .map_err(|_| self.error("invalid number"))?;
        if !float.is_finite() {
            return Err(self.error("non-finite number"));
        }
        Ok(Value::Float(float))
    }
}

fn write_value(out: &mut String, value: &Value) {
    match value {
        Value::Null => out.push_str("null"),
        Value::Bool(true) => out.push_str("true"),
        Value::Bool(false) => out.push_str("false"),
        Value::Int(integer) => {
            let _ = write!(out, "{integer}");
        }
        Value::Float(float) => {
            if !float.is_finite() {
                out.push_str("null");
            } else {
                let text = format!("{float}");
                out.push_str(&text);
                if !text.contains('.') && !text.contains('e') && !text.contains('E') {
                    out.push_str(".0");
                }
            }
        }
        Value::String(text) => write_string(out, text),
        Value::Array(items) => {
            out.push('[');
            for (index, item) in items.iter().enumerate() {
                if index > 0 {
                    out.push(',');
                }
                write_value(out, item);
            }
            out.push(']');
        }
        Value::Object(pairs) => {
            out.push('{');
            for (index, (key, item)) in pairs.iter().enumerate() {
                if index > 0 {
                    out.push(',');
                }
                write_string(out, key);
                out.push(':');
                write_value(out, item);
            }
            out.push('}');
        }
    }
}

fn write_string(out: &mut String, text: &str) {
    out.push('"');
    for ch in text.chars() {
        match ch {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\u{0008}' => out.push_str("\\b"),
            '\u{000c}' => out.push_str("\\f"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            ch if (ch as u32) < 0x20 => {
                let _ = write!(out, "\\u{code:04x}", code = ch as u32);
            }
            ch => out.push(ch),
        }
    }
    out.push('"');
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn round_trip_keeps_key_order() {
        let raw = r#"{"b":1,"a":[true,null,"x"],"b":2}"#;
        let value = parse(raw).unwrap();
        assert_eq!(to_string(&value), r#"{"b":2,"a":[true,null,"x"]}"#);
    }

    #[test]
    fn rejects_trailing_comma_and_junk() {
        assert!(parse("[1,]").is_err());
        assert!(parse("1 2").is_err());
        assert!(parse("\"unterminated").is_err());
    }

    #[test]
    fn string_escapes() {
        let value = parse(r#""a\"b\n\u0041""#).unwrap();
        assert_eq!(value, Value::String("a\"b\nA".to_string()));
    }
}
