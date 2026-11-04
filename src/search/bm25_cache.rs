//! On-disk BM25 cache. Search reloads postings instead of stemming the corpus.

use super::Bm25;
use std::collections::HashMap;
use std::fs;
use std::path::Path;

const MAGIC: &[u8; 4] = b"BM25";

pub fn save(index: &Bm25, path: &Path, fingerprint: u64) -> Result<(), String> {
    let mut terms: Vec<&String> = index.doc_freqs.keys().collect();
    terms.sort();
    let mut term_at: HashMap<&str, u32> = HashMap::with_capacity(terms.len());
    for (index, term) in terms.iter().enumerate() {
        term_at.insert(term.as_str(), index as u32);
    }
    let mut docs: Vec<&String> = index.doc_lengths.keys().collect();
    docs.sort();
    let mut out = Vec::new();
    out.extend_from_slice(MAGIC);
    write_u32(&mut out, 1);
    write_u64(&mut out, fingerprint);
    write_f64(&mut out, index.k1);
    write_f64(&mut out, index.b);
    write_f64(&mut out, index.avg_dl);
    write_u32(&mut out, terms.len() as u32);
    for term in &terms {
        write_str(&mut out, term);
        write_u32(&mut out, index.doc_freqs[*term]);
    }
    write_u32(&mut out, docs.len() as u32);
    for id in &docs {
        write_str(&mut out, id);
        write_u32(&mut out, index.doc_lengths[*id] as u32);
        let freqs = &index.term_freqs[*id];
        let mut pairs: Vec<(u32, u32)> = freqs
            .iter()
            .map(|(term, tf)| (term_at[term.as_str()], *tf))
            .collect();
        pairs.sort_by_key(|(term, _)| *term);
        write_u32(&mut out, pairs.len() as u32);
        for (term, tf) in pairs {
            write_u32(&mut out, term);
            write_u32(&mut out, tf);
        }
    }
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|err| err.to_string())?;
    }
    let tmp = path.with_extension("bin.tmp");
    fs::write(&tmp, &out).map_err(|err| err.to_string())?;
    fs::rename(&tmp, path).map_err(|err| err.to_string())?;
    Ok(())
}

pub fn load(path: &Path, fingerprint: u64) -> Option<Bm25> {
    let bytes = fs::read(path).ok()?;
    let mut cursor = Cursor {
        bytes: &bytes,
        at: 0,
    };
    if cursor.take(4)? != MAGIC {
        return None;
    }
    if cursor.u32()? != 1 || cursor.u64()? != fingerprint {
        return None;
    }
    let k1 = cursor.f64()?;
    let b = cursor.f64()?;
    let avg_dl = cursor.f64()?;
    let term_count = cursor.u32()? as usize;
    let mut terms = Vec::with_capacity(term_count);
    let mut doc_freqs = HashMap::with_capacity(term_count);
    for _ in 0..term_count {
        let term = cursor.string()?;
        let df = cursor.u32()?;
        doc_freqs.insert(term.clone(), df);
        terms.push(term);
    }
    let doc_count = cursor.u32()? as usize;
    let mut doc_lengths = HashMap::with_capacity(doc_count);
    let mut term_freqs = HashMap::with_capacity(doc_count);
    for _ in 0..doc_count {
        let id = cursor.string()?;
        let length = cursor.u32()? as usize;
        let n_pairs = cursor.u32()? as usize;
        let mut freqs = HashMap::with_capacity(n_pairs);
        for _ in 0..n_pairs {
            let term = cursor.u32()? as usize;
            let tf = cursor.u32()?;
            freqs.insert(terms.get(term)?.clone(), tf);
        }
        doc_lengths.insert(id.clone(), length);
        term_freqs.insert(id, freqs);
    }
    if cursor.at != bytes.len() {
        return None;
    }
    Some(Bm25 {
        k1,
        b,
        doc_freqs,
        doc_lengths,
        term_freqs,
        avg_dl,
        stem_cache: HashMap::new(),
    })
}

struct Cursor<'a> {
    bytes: &'a [u8],
    at: usize,
}

impl<'a> Cursor<'a> {
    fn take(&mut self, n: usize) -> Option<&'a [u8]> {
        let end = self.at.checked_add(n)?;
        let slice = self.bytes.get(self.at..end)?;
        self.at = end;
        Some(slice)
    }
    fn u32(&mut self) -> Option<u32> {
        Some(u32::from_le_bytes(self.take(4)?.try_into().ok()?))
    }
    fn u64(&mut self) -> Option<u64> {
        Some(u64::from_le_bytes(self.take(8)?.try_into().ok()?))
    }
    fn f64(&mut self) -> Option<f64> {
        Some(f64::from_le_bytes(self.take(8)?.try_into().ok()?))
    }
    fn string(&mut self) -> Option<String> {
        let len = self.u32()? as usize;
        let bytes = self.take(len)?;
        Some(String::from_utf8(bytes.to_vec()).ok()?)
    }
}

fn write_u32(out: &mut Vec<u8>, value: u32) {
    out.extend_from_slice(&value.to_le_bytes());
}
fn write_u64(out: &mut Vec<u8>, value: u64) {
    out.extend_from_slice(&value.to_le_bytes());
}
fn write_f64(out: &mut Vec<u8>, value: f64) {
    out.extend_from_slice(&value.to_le_bytes());
}
fn write_str(out: &mut Vec<u8>, value: &str) {
    write_u32(out, value.len() as u32);
    out.extend_from_slice(value.as_bytes());
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn round_trip_keeps_scores() {
        let mut index = Bm25::new();
        index.add("a", "console markup render");
        index.add("b", "other markup");
        let path = std::env::temp_dir().join(format!("stele-bm25-{}.bin", std::process::id()));
        save(&index, &path, 99).unwrap();
        let loaded = load(&path, 99).unwrap();
        assert_eq!(
            index.search("console markup", 2),
            loaded.search("console markup", 2)
        );
        assert!(load(&path, 1).is_none());
        let _ = fs::remove_file(path);
    }
}
