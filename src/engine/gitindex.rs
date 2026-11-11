//! A git checkout's index (`.git/index`, versions 2, 3 and 4), read without
//! running git (2.4).
//!
//! A worktree overlay uses it to know a file is unchanged without reading
//! it. When a file still has the size and mtime its index entry recorded,
//! git vouches that its bytes are that entry's blob, so two checkouts whose
//! entries for a path name the same blob hold the same bytes. A fresh
//! `git worktree add` gives every file a new mtime; without this the first
//! overlay call read every file (about 18 s of a 20 s call on Specula, the
//! reads of just-written files, not the hashing).
//!
//! An entry stamped at or after the index file's own mtime is "racily
//! clean" (the file may have changed in the same tick) and proves nothing.
//! Anything this reader does not understand (a split index, a truncated
//! file, another signature) yields `None`, and the caller reads and hashes
//! the files instead.

use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::time::UNIX_EPOCH;

struct Entry {
    /// Seconds and nanoseconds of the file's mtime when git staged it.
    mtime: (u32, u32),
    /// The file size, truncated to 32 bits as git stores it.
    size: u32,
    oid: Vec<u8>,
}

pub struct GitIndex {
    entries: HashMap<String, Entry>,
    written: (u32, u32),
}

/// The git directory of the checkout at `root`: its `.git` directory, or
/// the directory its `.git` file names (a linked worktree).
pub fn git_dir(root: &Path) -> Option<PathBuf> {
    let dot = root.join(".git");
    let meta = fs::metadata(&dot).ok()?;
    if meta.is_dir() {
        return Some(dot);
    }
    let text = fs::read_to_string(&dot).ok()?;
    let rest = text
        .lines()
        .find_map(|line| line.trim().strip_prefix("gitdir:"))?;
    let dir = PathBuf::from(rest.trim());
    Some(if dir.is_absolute() {
        dir
    } else {
        root.join(dir)
    })
}

/// 32 for a repository with `extensions.objectFormat = sha256`, else 20.
fn oid_len(git_dir: &Path) -> usize {
    let common = match fs::read_to_string(git_dir.join("commondir")) {
        Ok(rel) => git_dir.join(rel.trim()),
        Err(_) => git_dir.to_path_buf(),
    };
    let config = fs::read_to_string(common.join("config")).unwrap_or_default();
    let sha256 = config.lines().any(|line| {
        line.trim().to_ascii_lowercase().replace([' ', '\t'], "") == "objectformat=sha256"
    });
    if sha256 {
        32
    } else {
        20
    }
}

fn be32(data: &[u8], at: usize) -> Option<u32> {
    let bytes = data.get(at..at.checked_add(4)?)?;
    Some(u32::from_be_bytes([bytes[0], bytes[1], bytes[2], bytes[3]]))
}

fn be16(data: &[u8], at: usize) -> Option<u16> {
    let bytes = data.get(at..at.checked_add(2)?)?;
    Some(u16::from_be_bytes([bytes[0], bytes[1]]))
}

fn stamp_of(meta: &fs::Metadata) -> Option<(u32, u32)> {
    let since = meta.modified().ok()?.duration_since(UNIX_EPOCH).ok()?;
    Some((since.as_secs() as u32, since.subsec_nanos()))
}

/// The same instant as git records it: seconds always, nanoseconds only
/// when git stored them (a build without nanosecond stamps writes 0).
fn same_time(entry: (u32, u32), file: (u32, u32)) -> bool {
    entry.0 == file.0 && (entry.1 == 0 || entry.1 == file.1)
}

fn at_or_after(entry: (u32, u32), written: (u32, u32)) -> bool {
    if entry.1 == 0 || written.1 == 0 {
        entry.0 >= written.0
    } else {
        entry >= written
    }
}

impl GitIndex {
    /// The index of the checkout at `root`.
    pub fn read(root: &Path) -> Option<GitIndex> {
        let dir = git_dir(root)?;
        let path = dir.join("index");
        let written = stamp_of(&fs::metadata(&path).ok()?)?;
        let data = fs::read(&path).ok()?;
        GitIndex::parse(&data, oid_len(&dir), written)
    }

    fn parse(data: &[u8], oid_len: usize, written: (u32, u32)) -> Option<GitIndex> {
        if data.get(..4)? != b"DIRC" {
            return None;
        }
        let version = be32(data, 4)?;
        if !(2..=4).contains(&version) {
            return None;
        }
        let count = be32(data, 8)? as usize;
        let mut entries = HashMap::with_capacity(count);
        let mut pos = 12usize;
        let mut previous: Vec<u8> = Vec::new();
        for _ in 0..count {
            let mtime = (be32(data, pos + 8)?, be32(data, pos + 12)?);
            let size = be32(data, pos + 36)?;
            let oid = data.get(pos + 40..pos + 40 + oid_len)?.to_vec();
            let flags = be16(data, pos + 40 + oid_len)?;
            let mut name_at = pos + 40 + oid_len + 2;
            if flags & 0x4000 != 0 {
                if version < 3 {
                    return None;
                }
                name_at += 2;
            }
            let stage = (flags >> 12) & 0x3;
            let name: Vec<u8> = if version == 4 {
                // The path is the previous one with N bytes cut off its end
                // (N a git varint), then a NUL-terminated suffix; no padding.
                let mut at = name_at;
                let mut byte = *data.get(at)?;
                at += 1;
                let mut strip = (byte & 0x7f) as usize;
                while byte & 0x80 != 0 {
                    byte = *data.get(at)?;
                    at += 1;
                    strip = ((strip + 1) << 7) | (byte & 0x7f) as usize;
                }
                let keep = previous.len().checked_sub(strip)?;
                let end = at + data.get(at..)?.iter().position(|item| *item == 0)?;
                let mut name = previous[..keep].to_vec();
                name.extend_from_slice(&data[at..end]);
                pos = end + 1;
                name
            } else {
                let short = (flags & 0x0fff) as usize;
                let len = if short < 0x0fff {
                    short
                } else {
                    data.get(name_at..)?.iter().position(|item| *item == 0)?
                };
                let name = data.get(name_at..name_at + len)?.to_vec();
                // Entries are NUL-padded to a multiple of 8 bytes, at least one NUL.
                pos += (name_at - pos + len + 8) & !7;
                name
            };
            if stage == 0 {
                if let Ok(text) = std::str::from_utf8(&name) {
                    entries.insert(text.to_string(), Entry { mtime, size, oid });
                }
            }
            previous = name;
        }
        // Extensions, up to the trailing checksum. A split index keeps most
        // entries in a shared file this reader does not follow.
        let end = data.len().checked_sub(oid_len)?;
        while pos + 8 <= end {
            let signature = data.get(pos..pos + 4)?;
            if signature == b"link" {
                return None;
            }
            let len = be32(data, pos + 4)? as usize;
            pos = pos.checked_add(8 + len)?;
        }
        Some(GitIndex { entries, written })
    }

    /// The blob id git recorded for `key` (a `/` path from the checkout
    /// root) when `meta` still has that entry's size and mtime and the entry
    /// is not racily clean. None proves nothing.
    pub fn fresh_blob(&self, key: &str, meta: &fs::Metadata) -> Option<&[u8]> {
        let entry = self.entries.get(key)?;
        if at_or_after(entry.mtime, self.written) {
            return None;
        }
        if meta.len() as u32 != entry.size || !same_time(entry.mtime, stamp_of(meta)?) {
            return None;
        }
        Some(&entry.oid)
    }

    #[cfg(test)]
    fn len(&self) -> usize {
        self.entries.len()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixed(name: &str, mtime: (u32, u32), size: u32, oid: u8, extended: bool) -> Vec<u8> {
        let mut out = Vec::new();
        for value in [0, 0, mtime.0, mtime.1, 0, 0, 0o100644, 0, 0, size] {
            out.extend_from_slice(&value.to_be_bytes());
        }
        out.extend_from_slice(&[oid; 20]);
        let mut flags = name.len() as u16;
        if extended {
            flags |= 0x4000;
        }
        out.extend_from_slice(&flags.to_be_bytes());
        let mut start = 62;
        if extended {
            out.extend_from_slice(&[0, 0]);
            start += 2;
        }
        out.extend_from_slice(name.as_bytes());
        out.resize((start + name.len() + 8) & !7, 0);
        out
    }

    fn v4(name_suffix: &str, strip: u8, mtime: (u32, u32), size: u32, oid: u8) -> Vec<u8> {
        let mut out = Vec::new();
        for value in [0, 0, mtime.0, mtime.1, 0, 0, 0o100644, 0, 0, size] {
            out.extend_from_slice(&value.to_be_bytes());
        }
        out.extend_from_slice(&[oid; 20]);
        out.extend_from_slice(&0u16.to_be_bytes());
        out.push(strip);
        out.extend_from_slice(name_suffix.as_bytes());
        out.push(0);
        out
    }

    fn index(version: u32, entries: &[Vec<u8>], extension: Option<&[u8; 4]>) -> Vec<u8> {
        let mut out = b"DIRC".to_vec();
        out.extend_from_slice(&version.to_be_bytes());
        out.extend_from_slice(&(entries.len() as u32).to_be_bytes());
        for entry in entries {
            out.extend_from_slice(entry);
        }
        if let Some(signature) = extension {
            out.extend_from_slice(signature);
            out.extend_from_slice(&4u32.to_be_bytes());
            out.extend_from_slice(&[0; 4]);
        }
        out.extend_from_slice(&[0; 20]);
        out
    }

    #[test]
    fn versions_two_three_and_four() {
        let v2 = index(
            2,
            &[
                fixed("src/a.rs", (100, 5), 10, 1, false),
                fixed("src/longer_name.rs", (101, 0), 20, 2, false),
            ],
            Some(b"TREE"),
        );
        let parsed = GitIndex::parse(&v2, 20, (200, 0)).unwrap();
        assert_eq!(parsed.len(), 2);
        assert_eq!(parsed.entries["src/longer_name.rs"].size, 20);
        let v3 = index(3, &[fixed("x/y.rs", (100, 0), 3, 9, true)], None);
        assert_eq!(
            GitIndex::parse(&v3, 20, (200, 0)).unwrap().entries["x/y.rs"].oid,
            vec![9; 20]
        );
        // "src/a.rs", then "src/b.rs" (strip 4: "a.rs"), then "src/bc/d.rs"
        // (strip 3: ".rs" of "src/b.rs").
        let four = index(
            4,
            &[
                v4("src/a.rs", 0, (1, 0), 1, 1),
                v4("b.rs", 4, (1, 0), 2, 2),
                v4("c/d.rs", 3, (1, 0), 3, 3),
            ],
            None,
        );
        let parsed = GitIndex::parse(&four, 20, (200, 0)).unwrap();
        let mut names: Vec<&String> = parsed.entries.keys().collect();
        names.sort();
        assert_eq!(names, ["src/a.rs", "src/b.rs", "src/bc/d.rs"]);
    }

    #[test]
    fn split_and_unknown_indexes_prove_nothing() {
        let split = index(2, &[fixed("a", (1, 0), 1, 1, false)], Some(b"link"));
        assert!(GitIndex::parse(&split, 20, (200, 0)).is_none());
        let mut bad = index(2, &[fixed("a", (1, 0), 1, 1, false)], None);
        bad[0] = b'X';
        assert!(GitIndex::parse(&bad, 20, (200, 0)).is_none());
        let truncated = index(2, &[fixed("a", (1, 0), 1, 1, false)], None);
        assert!(GitIndex::parse(&truncated[..30], 20, (200, 0)).is_none());
        assert!(GitIndex::parse(&index(5, &[], None), 20, (200, 0)).is_none());
    }

    #[test]
    fn a_fresh_blob_needs_the_same_stamp_and_a_settled_index() {
        let dir = std::env::temp_dir().join(format!("stele-gitindex-{}", std::process::id()));
        fs::create_dir_all(&dir).unwrap();
        let file = dir.join("f.rs");
        fs::write(&file, "abc").unwrap();
        let meta = fs::metadata(&file).unwrap();
        let stamp = stamp_of(&meta).unwrap();
        let data = index(2, &[fixed("f.rs", stamp, 3, 7, false)], None);
        let later = (stamp.0 + 10, 0);
        let parsed = GitIndex::parse(&data, 20, later).unwrap();
        assert_eq!(parsed.fresh_blob("f.rs", &meta), Some(&[7u8; 20][..]));
        assert_eq!(parsed.fresh_blob("g.rs", &meta), None);
        // Racily clean: the index was written in the same second.
        let racy = GitIndex::parse(&data, 20, (stamp.0, 0)).unwrap();
        assert_eq!(racy.fresh_blob("f.rs", &meta), None);
        // A different size.
        fs::write(&file, "abcd").unwrap();
        let changed = fs::metadata(&file).unwrap();
        assert_eq!(parsed.fresh_blob("f.rs", &changed), None);
        let _ = fs::remove_dir_all(&dir);
    }
}

/// Write a version 2 index at `path` with an entry per (key, file, blob
/// byte), stamped from each file, and set the index's own mtime `settle`
/// seconds after the newest entry so no entry is racily clean.
#[cfg(test)]
pub(crate) fn write_for_test(path: &Path, entries: &[(&str, &Path, u8)], settle: u64) {
    let mut out = b"DIRC".to_vec();
    out.extend_from_slice(&2u32.to_be_bytes());
    out.extend_from_slice(&(entries.len() as u32).to_be_bytes());
    let mut newest = UNIX_EPOCH;
    let mut sorted: Vec<&(&str, &Path, u8)> = entries.iter().collect();
    sorted.sort_by_key(|(key, _, _)| *key);
    for (key, file, oid) in sorted {
        let meta = fs::metadata(file).unwrap();
        let modified = meta.modified().unwrap();
        newest = newest.max(modified);
        let (secs, nanos) = stamp_of(&meta).unwrap();
        for value in [0, 0, secs, nanos, 0, 0, 0o100644, 0, 0, meta.len() as u32] {
            out.extend_from_slice(&value.to_be_bytes());
        }
        out.extend_from_slice(&[*oid; 20]);
        out.extend_from_slice(&(key.len() as u16).to_be_bytes());
        let start = out.len() - 62;
        out.extend_from_slice(key.as_bytes());
        let len = (62 + key.len() + 8) & !7;
        out.resize(start + len, 0);
    }
    out.extend_from_slice(&[0; 20]);
    fs::create_dir_all(path.parent().unwrap()).unwrap();
    fs::write(path, out).unwrap();
    let file = fs::File::options().write(true).open(path).unwrap();
    file.set_modified(newest + std::time::Duration::from_secs(settle))
        .unwrap();
}
