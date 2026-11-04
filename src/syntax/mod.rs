//! Shared parsing core. Languages supply tokens; the tree is lossless.

pub(crate) mod cst;
pub(crate) mod edit;
mod incremental;
pub mod query;

pub use cst::{Node, Tree, NONE};
pub use edit::Edit;
pub use incremental::reparse;
pub use query::{symbols_of, SymbolHit};

use crate::lang;

/// Parse `source` with the grammar for `extension` (no dot).
/// Unknown extensions use a single opaque file so the bytes still round-trip.
pub fn parse(extension: &str, source: &str) -> Tree {
    lang::parse(extension, source)
}

pub fn round_trip(extension: &str, source: &str) -> bool {
    parse(extension, source).render(source) == source
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_cached_corpus_file_round_trips() {
        let root = std::path::Path::new("parity/.cache");
        if !root.exists() {
            return;
        }
        let mut stack = vec![root.to_path_buf()];
        let mut files = 0usize;
        while let Some(dir) = stack.pop() {
            for entry in std::fs::read_dir(&dir).unwrap() {
                let path = entry.unwrap().path();
                if path.is_dir() {
                    stack.push(path);
                    continue;
                }
                let ext = path
                    .extension()
                    .and_then(|ext| ext.to_str())
                    .unwrap_or("")
                    .to_ascii_lowercase();
                let bytes = std::fs::read(&path).unwrap();
                let source = String::from_utf8_lossy(&bytes).into_owned();
                assert!(round_trip(&ext, &source), "round trip {}", path.display());
                files += 1;
            }
        }
        assert!(files > 400, "saw {files} corpus files");
    }
}

pub(crate) fn parse_extension(extension: &str, source: &str) -> Tree {
    parse(extension, source)
}
