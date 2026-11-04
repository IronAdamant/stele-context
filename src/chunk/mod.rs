//! Chunk ids, signatures, and the 1.5.2 text/code split.
//! Offsets are Unicode scalar counts, matching Python `len` on `str`.

mod code;
mod merge;
mod patterns;
mod python_alnum;
mod python_alnum_bmp;
mod signature;
mod text;
mod tokens;

pub use code::extension_of;
pub(crate) use python_alnum::is_python_word;
pub use signature::{chunk_id, content_sha256, signature, signature_f32_hex};
pub use tokens::estimate_tokens;

pub const CHUNK_SIZE: usize = 256;
pub const MAX_CHUNK_SIZE: usize = 4096;

const TEXT_EXT: &[&str] = &["txt", "md", "markdown", "rst", "adoc", "log", "csv", "tsv"];

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Produced {
    pub content: String,
    pub modality: String,
    pub start: usize,
    pub end: usize,
    pub path: String,
    pub index: usize,
    pub language: Option<String>,
}

pub fn read_python_text(bytes: &[u8]) -> String {
    String::from_utf8_lossy(bytes)
        .replace("\r\n", "\n")
        .replace('\r', "\n")
}

pub fn chunk_bytes(path: &str, bytes: &[u8]) -> Vec<Produced> {
    if crate::media::is_media(code::extension_of(path)) {
        return crate::media::chunk(path, bytes);
    }
    chunk_document(path, &read_python_text(bytes))
}

pub fn chunk_document(path: &str, content: &str) -> Vec<Produced> {
    let extension = code::extension_of(path);
    let (pieces, modality, language) = if TEXT_EXT.contains(&extension) {
        (
            text::chunk(content, CHUNK_SIZE, MAX_CHUNK_SIZE),
            "text".to_string(),
            None,
        )
    } else {
        let (pieces, language) = code::chunk(content, path, CHUNK_SIZE, MAX_CHUNK_SIZE);
        (pieces, "code".to_string(), language)
    };
    let produced = pieces
        .into_iter()
        .enumerate()
        .map(|(index, piece)| Produced {
            content: piece.content,
            modality: modality.clone(),
            start: piece.start,
            end: piece.end,
            path: path.to_string(),
            index,
            language: language.clone(),
        })
        .collect();
    merge::merge_similar(produced, MAX_CHUNK_SIZE)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::json::{self, Value};
    use std::path::Path;

    fn string_field(value: &Value, key: &str) -> String {
        let Value::Object(pairs) = value else {
            return String::new();
        };
        pairs
            .iter()
            .find(|(name, _)| name == key)
            .and_then(|(_, item)| match item {
                Value::String(text) => Some(text.clone()),
                _ => None,
            })
            .unwrap_or_default()
    }

    fn int_field(value: &Value, key: &str) -> i64 {
        let Value::Object(pairs) = value else {
            return 0;
        };
        pairs
            .iter()
            .find(|(name, _)| name == key)
            .and_then(|(_, item)| match item {
                Value::Int(number) => Some(*number),
                Value::Null => Some(0),
                _ => None,
            })
            .unwrap_or(0)
    }

    fn language_field(value: &Value) -> Option<String> {
        let Value::Object(pairs) = value else {
            return None;
        };
        pairs
            .iter()
            .find(|(name, _)| name == "language")
            .and_then(|(_, item)| match item {
                Value::String(text) => Some(text.clone()),
                Value::Null => None,
                _ => None,
            })
    }

    #[test]
    fn fixture_signatures_and_chunk_ids_match_python() {
        let golden = std::fs::read_to_string("parity/goldens/fixtures.json").unwrap();
        let value = json::parse(&golden).unwrap();
        let Value::Object(root) = &value else {
            panic!("golden");
        };
        let files = root
            .iter()
            .find(|(key, _)| key == "files")
            .and_then(|(_, item)| match item {
                Value::Array(rows) => Some(rows),
                _ => None,
            })
            .unwrap();
        for file in files {
            let rel = string_field(file, "path");
            let bytes = std::fs::read(Path::new("parity/fixtures").join(&rel)).unwrap();
            let source = read_python_text(&bytes);
            let produced = chunk_document(&rel, &source);
            let chunks = match file {
                Value::Object(pairs) => pairs.iter().find(|(key, _)| key == "chunks"),
                _ => None,
            };
            let Some((_, Value::Array(chunks))) = chunks else {
                panic!("{rel}");
            };
            assert_eq!(produced.len(), chunks.len(), "{rel} chunk count");
            for (got, expected) in produced.iter().zip(chunks) {
                let digest = content_sha256(&got.content);
                assert_eq!(
                    got.start as i64,
                    int_field(expected, "start_pos"),
                    "{rel} start"
                );
                assert_eq!(got.end as i64, int_field(expected, "end_pos"), "{rel} end");
                assert_eq!(
                    digest,
                    string_field(expected, "content_sha256"),
                    "{rel} hash"
                );
                assert_eq!(
                    signature_f32_hex(&got.content),
                    string_field(expected, "signature_f32_le"),
                    "{rel} signature"
                );
                assert_eq!(
                    chunk_id(&rel, got.start, got.end, &digest),
                    string_field(expected, "chunk_id"),
                    "{rel} id"
                );
                assert_eq!(
                    estimate_tokens(&got.content) as i64,
                    int_field(expected, "token_count"),
                    "{rel} tokens"
                );
                assert_eq!(got.language, language_field(expected), "{rel} language");
                assert_eq!(
                    got.index as i64,
                    int_field(expected, "index"),
                    "{rel} index"
                );
            }
        }
    }
}
