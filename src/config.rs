//! `.stele-context.toml` subset: one section level, scalars, and arrays.
//! A broken file yields an empty config, matching the Python loader.

use std::fs;
use std::path::Path;

#[derive(Clone, Debug, Default, PartialEq)]
pub struct Config {
    pub storage_dir: Option<String>,
    pub chunk_size: Option<i64>,
    pub max_chunk_size: Option<i64>,
    pub merge_threshold: Option<f64>,
    pub change_threshold: Option<f64>,
    pub search_alpha: Option<f64>,
    pub skip_dirs: Option<Vec<String>>,
    pub respect_gitignore: Option<bool>,
    pub max_history_entries: Option<i64>,
    pub loaded: bool,
}

#[derive(Clone, Debug, PartialEq)]
enum TomlValue {
    String(String),
    Int(i64),
    Float(f64),
    Bool(bool),
    Array(Vec<TomlValue>),
}

pub fn load(project_root: Option<&Path>) -> Config {
    let Some(root) = project_root else {
        return Config::default();
    };
    let path = root.join(".stele-context.toml");
    let text = match fs::read_to_string(&path) {
        Ok(text) => text,
        Err(_) => return Config::default(),
    };
    let Ok(section) = parse_section(&text, "stele-context") else {
        return Config::default();
    };
    config_from_section(&section)
}

fn config_from_section(section: &[(String, TomlValue)]) -> Config {
    let mut config = Config {
        loaded: true,
        ..Config::default()
    };
    for (key, value) in section {
        match key.as_str() {
            "storage_dir" => {
                if let TomlValue::String(text) = value {
                    config.storage_dir = Some(text.clone());
                }
            }
            "chunk_size" => {
                if let TomlValue::Int(number) = value {
                    config.chunk_size = Some(*number);
                }
            }
            "max_chunk_size" => {
                if let TomlValue::Int(number) = value {
                    config.max_chunk_size = Some(*number);
                }
            }
            "merge_threshold" => config.merge_threshold = as_float(value),
            "change_threshold" => config.change_threshold = as_float(value),
            "search_alpha" => config.search_alpha = as_float(value),
            "max_history_entries" => {
                if let TomlValue::Int(number) = value {
                    config.max_history_entries = Some(*number);
                }
            }
            "respect_gitignore" => {
                if let TomlValue::Bool(flag) = value {
                    config.respect_gitignore = Some(*flag);
                }
            }
            "skip_dirs" => {
                if let TomlValue::Array(items) = value {
                    config.skip_dirs = Some(
                        items
                            .iter()
                            .filter_map(|item| match item {
                                TomlValue::String(text) => Some(text.clone()),
                                _ => None,
                            })
                            .collect(),
                    );
                }
            }
            _ => {}
        }
    }
    config
}

fn as_float(value: &TomlValue) -> Option<f64> {
    match value {
        TomlValue::Int(number) => Some(*number as f64),
        TomlValue::Float(number) => Some(*number),
        _ => None,
    }
}

fn parse_section(text: &str, wanted: &str) -> Result<Vec<(String, TomlValue)>, ()> {
    let mut current: Option<String> = None;
    let mut pairs = Vec::new();
    for raw in text.lines() {
        let line = strip_comment(raw).trim();
        if line.is_empty() {
            continue;
        }
        if line.starts_with('[') && line.ends_with(']') {
            current = Some(line[1..line.len() - 1].trim().to_string());
            continue;
        }
        if current.as_deref() != Some(wanted) {
            continue;
        }
        let Some((key, raw_value)) = line.split_once('=') else {
            return Err(());
        };
        let value = parse_value(raw_value.trim()).map_err(|_| ())?;
        pairs.push((key.trim().to_string(), value));
    }
    Ok(pairs)
}

fn strip_comment(line: &str) -> &str {
    let mut in_single = false;
    let mut in_double = false;
    let bytes = line.as_bytes();
    let mut index = 0;
    while index < bytes.len() {
        let byte = bytes[index];
        if byte == b'\'' && !in_double {
            in_single = !in_single;
        } else if byte == b'"' && !in_single {
            in_double = !in_double;
        } else if byte == b'#' && !in_single && !in_double {
            return &line[..index];
        } else if byte == b'\\' && in_double {
            index += 1;
        }
        index += 1;
    }
    line
}

fn parse_value(text: &str) -> Result<TomlValue, ()> {
    if text.is_empty() {
        return Err(());
    }
    if text == "true" {
        return Ok(TomlValue::Bool(true));
    }
    if text == "false" {
        return Ok(TomlValue::Bool(false));
    }
    if let Some(body) = text
        .strip_prefix('"')
        .and_then(|rest| rest.strip_suffix('"'))
    {
        return Ok(TomlValue::String(unescape(body)?));
    }
    if let Some(body) = text
        .strip_prefix('\'')
        .and_then(|rest| rest.strip_suffix('\''))
    {
        return Ok(TomlValue::String(body.to_string()));
    }
    if text.starts_with('[') && text.ends_with(']') {
        let inner = text[1..text.len() - 1].trim();
        if inner.is_empty() {
            return Ok(TomlValue::Array(Vec::new()));
        }
        let mut items = Vec::new();
        for part in split_commas(inner) {
            items.push(parse_value(part.trim())?);
        }
        return Ok(TomlValue::Array(items));
    }
    if text.contains('.') || text.contains('e') || text.contains('E') {
        let number: f64 = text.parse().map_err(|_| ())?;
        if number.is_finite() {
            return Ok(TomlValue::Float(number));
        }
        return Err(());
    }
    let number: i64 = text.parse().map_err(|_| ())?;
    Ok(TomlValue::Int(number))
}

fn split_commas(text: &str) -> Vec<&str> {
    let mut parts = Vec::new();
    let mut start = 0;
    let mut in_string = false;
    for (index, ch) in text.char_indices() {
        match ch {
            '"' | '\'' => in_string = !in_string,
            ',' if !in_string => {
                parts.push(&text[start..index]);
                start = index + 1;
            }
            _ => {}
        }
    }
    parts.push(&text[start..]);
    parts
}

fn unescape(text: &str) -> Result<String, ()> {
    let mut out = String::new();
    let mut chars = text.chars();
    while let Some(ch) = chars.next() {
        if ch != '\\' {
            out.push(ch);
            continue;
        }
        match chars.next() {
            Some('n') => out.push('\n'),
            Some('t') => out.push('\t'),
            Some('r') => out.push('\r'),
            Some('\\') => out.push('\\'),
            Some('"') => out.push('"'),
            _ => return Err(()),
        }
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reads_the_documented_subset() {
        let text = r#"
# comment
[stele-context]
storage_dir = "mem"
chunk_size = 256
merge_threshold = 0.7
respect_gitignore = false
skip_dirs = ["dist", "build"] # inline
"#;
        let section = parse_section(text, "stele-context").unwrap();
        let config = config_from_section(&section);
        assert_eq!(config.storage_dir.as_deref(), Some("mem"));
        assert_eq!(config.chunk_size, Some(256));
        assert_eq!(config.merge_threshold, Some(0.7));
        assert_eq!(config.respect_gitignore, Some(false));
        assert_eq!(
            config.skip_dirs,
            Some(vec!["dist".to_string(), "build".to_string()])
        );
    }
}
