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
    let streams = content_streams(bytes);
    if streams.is_empty() {
        return vec![(format!("pdf pages 0 bytes {}", bytes.len()), 0, bytes.len())];
    }
    streams
        .into_iter()
        .enumerate()
        .map(|(index, text)| {
            let text = if text.ends_with('\n') {
                text
            } else {
                format!("{text}\n")
            };
            let body = format!("[Page {}]\n{text}", index + 1);
            let end = (index + 1) * 1000;
            (body, index * 1000, end)
        })
        .collect()
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
    fn hex_tj_string_is_page_text() {
        let pdf = b"%PDF-1.4\nstream\nBT [<48656c6c6f20504446>] TJ ET\nendstream\n";
        let pages = pages(pdf);
        assert!(pages[0].0.contains("Hello PDF"), "{}", pages[0].0);
    }
}
