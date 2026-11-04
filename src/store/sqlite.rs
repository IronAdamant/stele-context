//! Read-only SQLite reader for a Python 1.5.x `stele_context.db`.
//!
//! Table b-trees, overflow pages, and a WAL overlay. Index b-trees are
//! skipped. The original file is not written.

use std::fs;
use std::path::Path;

#[derive(Clone, Debug)]
pub enum SqlValue {
    Null,
    Int(i64),
    Float(f64),
    Text(String),
    Blob(Vec<u8>),
}

pub struct SqliteDb {
    pages: Vec<Vec<u8>>,
    usable: usize,
}

impl SqliteDb {
    pub fn open(path: &Path) -> Result<SqliteDb, String> {
        let mut bytes = fs::read(path).map_err(|err| format!("read db: {err}"))?;
        if bytes.len() < 100 || &bytes[0..16] != b"SQLite format 3\0" {
            return Err("not a sqlite database".to_string());
        }
        let mut page_size = u16::from_be_bytes([bytes[16], bytes[17]]) as usize;
        if page_size == 1 {
            page_size = 65536;
        }
        if page_size < 512 || (!page_size.is_power_of_two() && page_size != 65536) {
            return Err(format!("bad page size {page_size}"));
        }
        let reserved = bytes[20] as usize;
        apply_wal(path, &mut bytes, page_size)?;
        if bytes.len() < page_size {
            return Err("database shorter than one page".to_string());
        }
        let mut pages = vec![Vec::new()];
        let mut offset = 0usize;
        while offset + page_size <= bytes.len() {
            pages.push(bytes[offset..offset + page_size].to_vec());
            offset += page_size;
        }
        Ok(SqliteDb {
            pages,
            usable: page_size - reserved,
        })
    }

    pub fn table(&self, name: &str) -> Result<Vec<Vec<(String, SqlValue)>>, String> {
        let master = self.walk_table(1)?;
        let mut root = 0usize;
        let mut sql = String::new();
        for row in master {
            let kind = text_at(&row, 0);
            let table = text_at(&row, 1);
            if kind == "table" && table == name {
                root = int_at(&row, 3) as usize;
                sql = text_at(&row, 4);
                break;
            }
        }
        if root == 0 {
            return Ok(Vec::new());
        }
        let columns = column_names(&sql);
        let cells = self.walk_table(root)?;
        let mut out = Vec::new();
        for cell in cells {
            let mut pairs = Vec::new();
            for (index, value) in cell.into_iter().enumerate() {
                let key = columns
                    .get(index)
                    .cloned()
                    .unwrap_or_else(|| format!("col{index}"));
                pairs.push((key, value));
            }
            out.push(pairs);
        }
        Ok(out)
    }

    fn walk_table(&self, page: usize) -> Result<Vec<Vec<SqlValue>>, String> {
        let mut out = Vec::new();
        self.walk_page(page, &mut out)?;
        Ok(out)
    }

    fn walk_page(&self, page_no: usize, out: &mut Vec<Vec<SqlValue>>) -> Result<(), String> {
        let page = self
            .pages
            .get(page_no)
            .ok_or_else(|| format!("missing page {page_no}"))?;
        let header = if page_no == 1 { 100 } else { 0 };
        if page.len() < header + 8 {
            return Err(format!("short page {page_no}"));
        }
        let kind = page[header];
        let interior = kind == 0x05 || kind == 0x02;
        let cell_count = u16::from_be_bytes([page[header + 3], page[header + 4]]) as usize;
        let ptr_start = header + if interior { 12 } else { 8 };
        if kind != 0x0d && kind != 0x05 {
            return Ok(());
        }
        let mut children = Vec::new();
        let mut leaves = Vec::new();
        for index in 0..cell_count {
            let at = ptr_start + index * 2;
            if at + 2 > page.len() {
                break;
            }
            let cell = u16::from_be_bytes([page[at], page[at + 1]]) as usize;
            if interior {
                if cell + 4 > page.len() {
                    continue;
                }
                let child = u32::from_be_bytes(page[cell..cell + 4].try_into().unwrap()) as usize;
                children.push(child);
            } else {
                leaves.push(cell);
            }
        }
        if interior {
            for child in children {
                self.walk_page(child, out)?;
            }
            let right = u32::from_be_bytes([
                page[header + 8],
                page[header + 9],
                page[header + 10],
                page[header + 11],
            ]) as usize;
            if right != 0 {
                self.walk_page(right, out)?;
            }
        } else {
            for cell in leaves {
                if let Some(record) = self.leaf_record(page_no, cell) {
                    out.push(record);
                }
            }
        }
        Ok(())
    }

    fn leaf_record(&self, page_no: usize, cell: usize) -> Option<Vec<SqlValue>> {
        let page = self.pages.get(page_no)?;
        let (payload_len, next) = varint(page, cell)?;
        let (_rowid, mut cursor) = varint(page, next)?;
        let local = local_payload(payload_len, self.usable);
        let mut payload = Vec::new();
        let available = page.len().saturating_sub(cursor);
        let take = local.min(available);
        payload.extend_from_slice(&page[cursor..cursor + take]);
        cursor += take;
        if payload.len() < payload_len && cursor + 4 <= page.len() {
            let mut overflow =
                u32::from_be_bytes(page[cursor..cursor + 4].try_into().ok()?) as usize;
            while payload.len() < payload_len && overflow != 0 {
                let extra = self.pages.get(overflow)?;
                if extra.len() < 4 {
                    break;
                }
                let next_page = u32::from_be_bytes(extra[0..4].try_into().ok()?) as usize;
                let room = extra.len() - 4;
                let need = payload_len - payload.len();
                let n = need.min(room);
                payload.extend_from_slice(&extra[4..4 + n]);
                overflow = next_page;
            }
        }
        parse_record(&payload)
    }
}

fn local_payload(payload: usize, usable: usize) -> usize {
    let max_local = usable.saturating_sub(35);
    if payload <= max_local {
        return payload;
    }
    let min_local = ((usable.saturating_sub(12)) * 32 / 255).saturating_sub(23);
    let leftover = (payload - min_local) % usable.saturating_sub(4);
    let candidate = min_local + leftover;
    if candidate <= max_local {
        candidate
    } else {
        min_local
    }
}

fn parse_record(payload: &[u8]) -> Option<Vec<SqlValue>> {
    let (header_size, mut cursor) = varint(payload, 0)?;
    if header_size > payload.len() {
        return None;
    }
    let header_end = header_size;
    let mut types = Vec::new();
    while cursor < header_end {
        let (serial, next) = varint(payload, cursor)?;
        types.push(serial);
        cursor = next;
    }
    let mut body = header_end;
    let mut values = Vec::new();
    for serial in types {
        let (value, next) = read_serial(payload, body, serial as u64)?;
        values.push(value);
        body = next;
    }
    Some(values)
}

fn read_serial(payload: &[u8], at: usize, serial: u64) -> Option<(SqlValue, usize)> {
    match serial {
        0 => Some((SqlValue::Null, at)),
        1 => Some((SqlValue::Int(byte(payload, at)? as i8 as i64), at + 1)),
        2 => Some((
            SqlValue::Int(i16::from_be_bytes(slice2(payload, at)?) as i64),
            at + 2,
        )),
        3 => Some((SqlValue::Int(be24(payload, at)?), at + 3)),
        4 => Some((
            SqlValue::Int(i32::from_be_bytes(slice4(payload, at)?) as i64),
            at + 4,
        )),
        5 => Some((SqlValue::Int(be48(payload, at)?), at + 6)),
        6 => Some((
            SqlValue::Int(i64::from_be_bytes(slice8(payload, at)?)),
            at + 8,
        )),
        7 => {
            let bits = u64::from_be_bytes(slice8(payload, at)?);
            Some((SqlValue::Float(f64::from_bits(bits)), at + 8))
        }
        8 => Some((SqlValue::Int(0), at)),
        9 => Some((SqlValue::Int(1), at)),
        n if n >= 12 && n % 2 == 0 => {
            let len = ((n - 12) / 2) as usize;
            let end = at + len;
            if end > payload.len() {
                return None;
            }
            Some((SqlValue::Blob(payload[at..end].to_vec()), end))
        }
        n if n >= 13 && n % 2 == 1 => {
            let len = ((n - 13) / 2) as usize;
            let end = at + len;
            if end > payload.len() {
                return None;
            }
            let text = String::from_utf8_lossy(&payload[at..end]).into_owned();
            Some((SqlValue::Text(text), end))
        }
        _ => None,
    }
}

fn apply_wal(db: &Path, bytes: &mut Vec<u8>, page_size: usize) -> Result<(), String> {
    let wal_path = db.with_file_name(format!(
        "{}-wal",
        db.file_name()
            .and_then(|name| name.to_str())
            .unwrap_or("db")
    ));
    if !wal_path.is_file() {
        return Ok(());
    }
    let wal = fs::read(wal_path).map_err(|err| format!("read wal: {err}"))?;
    if wal.len() < 32 {
        return Ok(());
    }
    let magic = u32::from_be_bytes(wal[0..4].try_into().unwrap());
    if magic != 0x377f_0682 && magic != 0x377f_0683 {
        return Ok(());
    }
    let salt1 = u32::from_be_bytes(wal[16..20].try_into().unwrap());
    let salt2 = u32::from_be_bytes(wal[20..24].try_into().unwrap());
    let frame = 24 + page_size;
    let mut offset = 32usize;
    while offset + frame <= wal.len() {
        let page_no = u32::from_be_bytes(wal[offset..offset + 4].try_into().unwrap()) as usize;
        let frame_salt1 = u32::from_be_bytes(wal[offset + 8..offset + 12].try_into().unwrap());
        let frame_salt2 = u32::from_be_bytes(wal[offset + 12..offset + 16].try_into().unwrap());
        if frame_salt1 != salt1 || frame_salt2 != salt2 || page_no == 0 {
            break;
        }
        let start = (page_no - 1) * page_size;
        let end = start + page_size;
        if bytes.len() < end {
            bytes.resize(end, 0);
        }
        bytes[start..end].copy_from_slice(&wal[offset + 24..offset + 24 + page_size]);
        offset += frame;
    }
    Ok(())
}

fn column_names(sql: &str) -> Vec<String> {
    let Some(open) = sql.find('(') else {
        return Vec::new();
    };
    let mut depth = 0i32;
    let mut body = String::new();
    for ch in sql[open + 1..].chars() {
        if ch == '(' {
            depth += 1;
            body.push(ch);
        } else if ch == ')' {
            if depth == 0 {
                break;
            }
            depth -= 1;
            body.push(ch);
        } else {
            body.push(ch);
        }
    }
    let mut names = Vec::new();
    let mut piece = String::new();
    let mut depth = 0i32;
    for ch in body.chars() {
        if ch == '(' {
            depth += 1;
            piece.push(ch);
        } else if ch == ')' {
            depth -= 1;
            piece.push(ch);
        } else if ch == ',' && depth == 0 {
            push_column(&mut names, &piece);
            piece.clear();
        } else {
            piece.push(ch);
        }
    }
    push_column(&mut names, &piece);
    names
}

fn push_column(names: &mut Vec<String>, piece: &str) {
    let token = piece.split_whitespace().next().unwrap_or("");
    let token = token.trim_matches('"').trim_matches('`').trim_matches('[');
    let lower = token.to_ascii_lowercase();
    if token.is_empty()
        || matches!(
            lower.as_str(),
            "primary" | "unique" | "check" | "foreign" | "constraint"
        )
    {
        return;
    }
    names.push(token.to_string());
}

fn varint(bytes: &[u8], mut at: usize) -> Option<(usize, usize)> {
    let mut value = 0u64;
    for _ in 0..9 {
        if at >= bytes.len() {
            return None;
        }
        let byte = bytes[at];
        at += 1;
        if value > (u64::MAX >> 7) {
            return None;
        }
        value = (value << 7) | u64::from(byte & 0x7f);
        if byte & 0x80 == 0 {
            return Some((value as usize, at));
        }
    }
    None
}

fn byte(payload: &[u8], at: usize) -> Option<u8> {
    payload.get(at).copied()
}

fn slice2(payload: &[u8], at: usize) -> Option<[u8; 2]> {
    payload.get(at..at + 2)?.try_into().ok()
}

fn slice4(payload: &[u8], at: usize) -> Option<[u8; 4]> {
    payload.get(at..at + 4)?.try_into().ok()
}

fn slice8(payload: &[u8], at: usize) -> Option<[u8; 8]> {
    payload.get(at..at + 8)?.try_into().ok()
}

fn be24(payload: &[u8], at: usize) -> Option<i64> {
    let b = payload.get(at..at + 3)?;
    let mut v = i32::from(b[0]) << 16 | i32::from(b[1]) << 8 | i32::from(b[2]);
    if v & 0x80_0000 != 0 {
        v |= !0xFF_FFFF;
    }
    Some(i64::from(v))
}

fn be48(payload: &[u8], at: usize) -> Option<i64> {
    let b = payload.get(at..at + 6)?;
    let mut v = 0i64;
    for byte in b {
        v = (v << 8) | i64::from(*byte);
    }
    if b[0] & 0x80 != 0 {
        v |= !0xFFFF_FFFF_FFFF;
    }
    Some(v)
}

fn text_at(row: &[SqlValue], index: usize) -> String {
    match row.get(index) {
        Some(SqlValue::Text(text)) => text.clone(),
        Some(SqlValue::Int(number)) => number.to_string(),
        _ => String::new(),
    }
}

fn int_at(row: &[SqlValue], index: usize) -> i64 {
    match row.get(index) {
        Some(SqlValue::Int(number)) => *number,
        _ => 0,
    }
}

pub fn field<'a>(row: &'a [(String, SqlValue)], name: &str) -> Option<&'a SqlValue> {
    row.iter()
        .find(|(key, _)| key == name)
        .map(|(_, value)| value)
}

pub fn text(row: &[(String, SqlValue)], name: &str) -> String {
    match field(row, name) {
        Some(SqlValue::Text(text)) => text.clone(),
        Some(SqlValue::Int(number)) => number.to_string(),
        Some(SqlValue::Float(number)) => number.to_string(),
        _ => String::new(),
    }
}

pub fn number(row: &[(String, SqlValue)], name: &str) -> f64 {
    match field(row, name) {
        Some(SqlValue::Int(number)) => *number as f64,
        Some(SqlValue::Float(number)) => *number,
        Some(SqlValue::Text(text)) => text.parse().unwrap_or(0.0),
        _ => 0.0,
    }
}

pub fn blob(row: &[(String, SqlValue)], name: &str) -> Vec<u8> {
    match field(row, name) {
        Some(SqlValue::Blob(bytes)) => bytes.clone(),
        _ => Vec::new(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::process::Command;

    #[test]
    fn reads_a_python_sqlite_table() {
        let dir = std::env::temp_dir().join(format!("stele-sqlite-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        let db = dir.join("sample.db");
        let script = r#"
import sqlite3, sys
con = sqlite3.connect(sys.argv[1])
con.execute("create table documents (document_path text primary key, content_hash text, file_size integer)")
con.execute("insert into documents values ('a.py', 'abc', 12)")
con.commit()
"#;
        let status = Command::new(python())
            .arg("-c")
            .arg(script)
            .arg(&db)
            .status()
            .expect("python");
        assert!(status.success());
        let opened = SqliteDb::open(&db).unwrap();
        let rows = opened.table("documents").unwrap();
        assert_eq!(rows.len(), 1);
        assert_eq!(text(&rows[0], "document_path"), "a.py");
        assert_eq!(number(&rows[0], "file_size") as i64, 12);
        let _ = fs::remove_dir_all(&dir);
    }

    fn python() -> &'static str {
        if Command::new("python3").arg("--version").output().is_ok() {
            "python3"
        } else {
            "python"
        }
    }
}
