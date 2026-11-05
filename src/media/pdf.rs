//! Page text from uncompressed and FlateDecode PDF streams.
//!
//! PyMuPDF's layout, fonts, and reading order are not reproduced. The chunk
//! text is `[Page N]` plus the strings drawn by `Tj` / `TJ` / `'`, which is
//! enough for uncompressed and Flate text pages to be searchable.

use super::inflate;

pub fn pages(bytes: &[u8]) -> Vec<(String, usize, usize)> {
    if !bytes.starts_with(b"%PDF") {
        return vec![(format!("pdf bytes {}", bytes.len()), 0, bytes.len())];
    }
    // One chunk per page. A page may reference several content streams
    // (two text lines often do). Those streams are one page, not two.
    let mut texts = page_texts(bytes);
    if texts.is_empty() {
        let joined = content_streams(bytes).join("\n");
        if joined.trim().is_empty() {
            return vec![(format!("pdf pages 0 bytes {}", bytes.len()), 0, bytes.len())];
        }
        texts.push(joined);
    }
    let chunks: Vec<(String, usize, usize)> = texts
        .iter()
        .enumerate()
        .filter_map(|(index, text)| {
            if text.trim().is_empty() {
                return None;
            }
            let text = if text.ends_with('\n') {
                text.clone()
            } else {
                format!("{text}\n")
            };
            let body = format!("[Page {}]\n{text}", index + 1);
            let end = (index + 1) * 1000;
            Some((body, index * 1000, end))
        })
        .collect();
    if !chunks.is_empty() {
        return chunks;
    }
    let joined = content_streams(bytes).join("\n");
    if joined.trim().is_empty() {
        return vec![(format!("pdf pages 0 bytes {}", bytes.len()), 0, bytes.len())];
    }
    vec![(format!("[Page 1]\n{joined}\n"), 0, 1000)]
}

struct PdfObj {
    num: u32,
    body: Vec<u8>,
    stream: Vec<u8>,
}

fn page_texts(bytes: &[u8]) -> Vec<String> {
    let objects = pdf_objects(bytes);
    if objects.is_empty() {
        return Vec::new();
    }
    let mut stream_text = std::collections::HashMap::new();
    let mut pages = Vec::new();
    let mut kids = Vec::new();
    for obj in &objects {
        let body = String::from_utf8_lossy(&obj.body);
        if body.contains("/Type /Pages") || body.contains("/Type/Pages") {
            kids = pdf_refs_after(&body, "/Kids");
        }
        if is_page(&body) {
            pages.push((obj.num, pdf_refs_after(&body, "/Contents")));
        }
        if !obj.stream.is_empty() {
            let header = &obj.body;
            let flate = find_sub(header, 0, b"FlateDecode").is_some();
            let decoded = if flate {
                inflate::zlib_decode(&obj.stream).unwrap_or_default()
            } else {
                obj.stream.clone()
            };
            stream_text.insert(obj.num, show_text(&decoded));
        }
    }
    if pages.is_empty() {
        return Vec::new();
    }
    let order: Vec<u32> = if kids.is_empty() {
        pages.iter().map(|(num, _)| *num).collect()
    } else {
        kids
    };
    let mut out = Vec::new();
    for page_num in order {
        let Some((_, contents)) = pages.iter().find(|(num, _)| *num == page_num) else {
            continue;
        };
        let mut text = String::new();
        for id in contents {
            let Some(piece) = stream_text.get(id) else {
                continue;
            };
            if piece.trim().is_empty() {
                continue;
            }
            if !text.is_empty() && !text.ends_with('\n') {
                text.push('\n');
            }
            text.push_str(piece);
        }
        // A blank page stays in the list so later pages keep their numbers.
        out.push(text);
    }
    out
}

fn is_page(body: &str) -> bool {
    (body.contains("/Type /Page") || body.contains("/Type/Page"))
        && !body.contains("/Type /Pages")
        && !body.contains("/Type/Pages")
}

fn pdf_objects(bytes: &[u8]) -> Vec<PdfObj> {
    let mut out = Vec::new();
    let mut cursor = 0usize;
    while let Some(rel) = find_sub(bytes, cursor, b" obj") {
        let at = cursor + rel;
        let mut i = at;
        while i > 0 && bytes[i - 1].is_ascii_whitespace() {
            i -= 1;
        }
        while i > 0 && bytes[i - 1].is_ascii_digit() {
            i -= 1;
        }
        while i > 0 && bytes[i - 1].is_ascii_whitespace() {
            i -= 1;
        }
        let num_end = i;
        while i > 0 && bytes[i - 1].is_ascii_digit() {
            i -= 1;
        }
        let num = std::str::from_utf8(&bytes[i..num_end])
            .ok()
            .and_then(|text| text.parse().ok())
            .unwrap_or(0);
        let body_start = at + b" obj".len();
        let Some(end_rel) = find_sub(bytes, body_start, b"endobj") else {
            break;
        };
        let body_end = body_start + end_rel;
        let region = &bytes[body_start..body_end];
        let (body, stream) = split_stream(region);
        out.push(PdfObj { num, body, stream });
        cursor = body_end + b"endobj".len();
    }
    out
}

fn split_stream(region: &[u8]) -> (Vec<u8>, Vec<u8>) {
    let Some(rel) = find_sub(region, 0, b"stream") else {
        return (region.to_vec(), Vec::new());
    };
    let mut start = rel + b"stream".len();
    if region.get(start) == Some(&b'\r') {
        start += 1;
    }
    if region.get(start) == Some(&b'\n') {
        start += 1;
    }
    let Some(end_rel) = find_sub(region, start, b"endstream") else {
        return (region[..rel].to_vec(), Vec::new());
    };
    let end = start + end_rel;
    (region[..rel].to_vec(), region[start..end].to_vec())
}

fn pdf_refs_after(body: &str, key: &str) -> Vec<u32> {
    let Some(at) = body.find(key) else {
        return Vec::new();
    };
    let rest = &body[at + key.len()..];
    let mut nums = Vec::new();
    let bytes = rest.as_bytes();
    let mut i = 0usize;
    while i < bytes.len() && bytes[i].is_ascii_whitespace() {
        i += 1;
    }
    let array = bytes.get(i) == Some(&b'[');
    if array {
        i += 1;
    }
    loop {
        while i < bytes.len() && bytes[i].is_ascii_whitespace() {
            i += 1;
        }
        if i >= bytes.len() || bytes[i] == b']' || bytes[i] == b'/' || bytes[i] == b'>' {
            break;
        }
        if !bytes[i].is_ascii_digit() {
            break;
        }
        let start = i;
        while i < bytes.len() && bytes[i].is_ascii_digit() {
            i += 1;
        }
        if let Ok(num) = rest[start..i].parse() {
            nums.push(num);
        }
        while i < bytes.len() && bytes[i].is_ascii_whitespace() {
            i += 1;
        }
        while i < bytes.len() && bytes[i].is_ascii_digit() {
            i += 1;
        }
        while i < bytes.len() && bytes[i].is_ascii_whitespace() {
            i += 1;
        }
        if bytes.get(i) == Some(&b'R') {
            i += 1;
        }
        if !array {
            break;
        }
    }
    nums
}

fn content_streams(bytes: &[u8]) -> Vec<String> {
    let mut out = Vec::new();
    let mut cursor = 0usize;
    while let Some(rel) = find_sub(bytes, cursor, b"stream") {
        let mut start = cursor + rel + b"stream".len();
        if bytes.get(start) == Some(&b'\r') {
            start += 1;
        }
        if bytes.get(start) == Some(&b'\n') {
            start += 1;
        }
        let Some(end_rel) = find_sub(bytes, start, b"endstream") else {
            break;
        };
        let end = start + end_rel;
        let raw = &bytes[start..end];
        let header = &bytes[cursor..cursor + rel];
        let flate = find_sub(header, 0, b"FlateDecode").is_some();
        let decoded = if flate {
            inflate::zlib_decode(raw).unwrap_or_default()
        } else {
            raw.to_vec()
        };
        let text = show_text(&decoded);
        if !text.trim().is_empty() {
            out.push(text);
        }
        cursor = end + b"endstream".len();
    }
    out
}

fn show_text(stream: &[u8]) -> String {
    let mut out = String::new();
    let mut index = 0usize;
    while index < stream.len() {
        if stream[index] == b'(' {
            if let Some((text, next)) = literal_string(stream, index) {
                push_text(&mut out, &text);
                index = next;
                continue;
            }
        }
        if stream[index] == b'<' && stream.get(index + 1) != Some(&b'<') {
            if let Some((text, next)) = hex_string(stream, index) {
                push_text(&mut out, &text);
                index = next;
                continue;
            }
        }
        index += 1;
    }
    out
}

fn push_text(out: &mut String, text: &str) {
    if text.is_empty() {
        return;
    }
    if !out.is_empty() {
        out.push(' ');
    }
    out.push_str(text);
}

fn hex_string(bytes: &[u8], start: usize) -> Option<(String, usize)> {
    let mut index = start + 1;
    let mut nibbles = Vec::new();
    while index < bytes.len() {
        let byte = bytes[index];
        if byte == b'>' {
            if nibbles.len() % 2 == 1 {
                nibbles.push(0);
            }
            let raw: Vec<u8> = nibbles
                .chunks(2)
                .map(|pair| (pair[0] << 4) | pair[1])
                .collect();
            return Some((String::from_utf8_lossy(&raw).into_owned(), index + 1));
        }
        if byte.is_ascii_hexdigit() {
            nibbles.push(match byte {
                b'0'..=b'9' => byte - b'0',
                b'a'..=b'f' => byte - b'a' + 10,
                _ => byte - b'A' + 10,
            });
            index += 1;
            continue;
        }
        if byte.is_ascii_whitespace() {
            index += 1;
            continue;
        }
        return None;
    }
    None
}

fn literal_string(bytes: &[u8], start: usize) -> Option<(String, usize)> {
    let mut index = start + 1;
    let mut raw = Vec::new();
    let mut escaped = false;
    while index < bytes.len() {
        let byte = bytes[index];
        if escaped {
            raw.push(match byte {
                b'n' => b'\n',
                b'r' => b'\r',
                b't' => b'\t',
                _ => byte,
            });
            escaped = false;
            index += 1;
            continue;
        }
        if byte == b'\\' {
            escaped = true;
            index += 1;
            continue;
        }
        if byte == b')' {
            let text = String::from_utf8_lossy(&raw).into_owned();
            return Some((text, index + 1));
        }
        raw.push(byte);
        index += 1;
    }
    None
}

fn find_sub(bytes: &[u8], from: usize, needle: &[u8]) -> Option<usize> {
    bytes[from..]
        .windows(needle.len())
        .position(|window| window == needle)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn uncompressed_page_text() {
        let pdf = b"%PDF-1.4\n\
1 0 obj << /Type /Page /Contents 2 0 R >> endobj\n\
2 0 obj << /Length 44 >>\n\
stream\n\
BT /F1 12 Tf 100 100 Td (Hello PDF) Tj ET\n\
endstream\n\
endobj\n\
%%EOF\n";
        let pages = pages(pdf);
        assert_eq!(pages.len(), 1);
        assert!(pages[0].0.contains("[Page 1]"), "{}", pages[0].0);
        assert!(pages[0].0.contains("Hello PDF"), "{}", pages[0].0);
    }

    #[test]
    fn two_streams_on_one_page_are_one_chunk() {
        let pdf = b"%PDF-1.4\n\
1 0 obj << /Type /Page /Contents [2 0 R 3 0 R] >> endobj\n\
2 0 obj << /Length 20 >>\nstream\nBT (Hello) Tj ET\nendstream\nendobj\n\
3 0 obj << /Length 20 >>\nstream\nBT (World) Tj ET\nendstream\nendobj\n\
%%EOF\n";
        let pages = pages(pdf);
        assert_eq!(
            pages.len(),
            1,
            "{:?}",
            pages.iter().map(|p| &p.0).collect::<Vec<_>>()
        );
        assert!(pages[0].0.contains("Hello"), "{}", pages[0].0);
        assert!(pages[0].0.contains("World"), "{}", pages[0].0);
        assert!(!pages[0].0.contains("[Page 2]"), "{}", pages[0].0);
    }

    #[test]
    fn hex_tj_string_is_page_text() {
        let pdf = b"%PDF-1.4\nstream\nBT [<48656c6c6f20504446>] TJ ET\nendstream\n";
        let pages = pages(pdf);
        assert!(pages[0].0.contains("Hello PDF"), "{}", pages[0].0);
    }
}
