//! Std-only image, audio, video, and PDF chunking.
//!
//! Python 1.5.2 produces these chunks only when Pillow, librosa, OpenCV, or
//! PyMuPDF import. Those libraries are not vendored. Rust indexes the same
//! extensions with header metadata and, for PDF, page text from uncompressed
//! and FlateDecode streams. Pixel hashes, MFCC, and decoded video frames are
//! not reproduced; see the module docs on each chunker.

mod inflate;
mod pdf;

use crate::chunk::{content_sha256, estimate_tokens, signature_f32_hex, Produced};

pub fn is_media(ext: &str) -> bool {
    matches!(
        ext,
        "png"
            | "jpg"
            | "jpeg"
            | "gif"
            | "webp"
            | "bmp"
            | "tiff"
            | "tif"
            | "ico"
            | "mp3"
            | "wav"
            | "ogg"
            | "flac"
            | "m4a"
            | "aac"
            | "wma"
            | "mp4"
            | "mov"
            | "mkv"
            | "webm"
            | "avi"
            | "pdf"
    )
}

pub fn chunk(path: &str, bytes: &[u8]) -> Vec<Produced> {
    let ext = ext_of(path);
    let pieces = match ext {
        "pdf" => pdf::pages(bytes),
        "wav" => audio_segments(bytes),
        "mp4" | "mov" | "mkv" | "webm" | "avi" => vec![video_note(bytes, ext)],
        "mp3" | "ogg" | "flac" | "m4a" | "aac" | "wma" => vec![audio_note(bytes, ext)],
        _ => vec![image_note(bytes, ext)],
    };
    let modality = if ext == "pdf" {
        "pdf"
    } else if matches!(ext, "mp3" | "wav" | "ogg" | "flac" | "m4a" | "aac" | "wma") {
        "audio"
    } else if matches!(ext, "mp4" | "mov" | "mkv" | "webm" | "avi") {
        "video"
    } else {
        "image"
    };
    pieces
        .into_iter()
        .enumerate()
        .map(|(index, (content, start, end))| Produced {
            content,
            modality: modality.to_string(),
            start,
            end,
            path: path.to_string(),
            index,
            language: None,
        })
        .collect()
}

pub fn signature_of(content: &str) -> (String, String, usize) {
    (
        content_sha256(content),
        signature_f32_hex(content),
        estimate_tokens(content).max(1),
    )
}

fn image_note(bytes: &[u8], ext: &str) -> (String, usize, usize) {
    let (width, height) = image_size(bytes).unwrap_or((0, 0));
    let text = format!("image {ext} {width}x{height} bytes {}", bytes.len());
    (text, 0, bytes.len())
}

fn image_size(bytes: &[u8]) -> Option<(u32, u32)> {
    if bytes.starts_with(b"\x89PNG\r\n\x1a\n") && bytes.len() >= 24 {
        let width = u32::from_be_bytes(bytes[16..20].try_into().ok()?);
        let height = u32::from_be_bytes(bytes[20..24].try_into().ok()?);
        return Some((width, height));
    }
    if (bytes.starts_with(b"GIF87a") || bytes.starts_with(b"GIF89a")) && bytes.len() >= 10 {
        let width = u16::from_le_bytes(bytes[6..8].try_into().ok()?) as u32;
        let height = u16::from_le_bytes(bytes[8..10].try_into().ok()?) as u32;
        return Some((width, height));
    }
    if bytes.starts_with(&[0xff, 0xd8]) {
        return jpeg_size(bytes);
    }
    if bytes.len() >= 26 && &bytes[8..12] == b"WEBP" {
        return Some((0, 0));
    }
    None
}

fn jpeg_size(bytes: &[u8]) -> Option<(u32, u32)> {
    let mut index = 2usize;
    while index + 8 < bytes.len() {
        if bytes[index] != 0xff {
            index += 1;
            continue;
        }
        let marker = bytes[index + 1];
        if marker == 0xc0 || marker == 0xc2 {
            let height = u16::from_be_bytes(bytes[index + 5..index + 7].try_into().ok()?) as u32;
            let width = u16::from_be_bytes(bytes[index + 7..index + 9].try_into().ok()?) as u32;
            return Some((width, height));
        }
        let len = u16::from_be_bytes(bytes[index + 2..index + 4].try_into().ok()?) as usize;
        index += 2 + len;
    }
    None
}

fn audio_note(bytes: &[u8], ext: &str) -> (String, usize, usize) {
    (format!("audio {ext} bytes {}", bytes.len()), 0, bytes.len())
}

fn audio_segments(bytes: &[u8]) -> Vec<(String, usize, usize)> {
    let Some((rate, samples)) = wav_info(bytes) else {
        return vec![audio_note(bytes, "wav")];
    };
    let seconds = if rate == 0 { 0 } else { samples / rate };
    let step = 30u32;
    if seconds <= step {
        return vec![(
            format!("audio wav {rate}Hz {seconds}s bytes {}", bytes.len()),
            0,
            bytes.len(),
        )];
    }
    let mut out = Vec::new();
    let mut at = 0u32;
    while at < seconds {
        let end = (at + step).min(seconds);
        out.push((
            format!("audio wav {rate}Hz {at}-{end}s"),
            at as usize,
            end as usize,
        ));
        at = end;
    }
    out
}

fn wav_info(bytes: &[u8]) -> Option<(u32, u32)> {
    if bytes.len() < 44 || &bytes[0..4] != b"RIFF" || &bytes[8..12] != b"WAVE" {
        return None;
    }
    let rate = u32::from_le_bytes(bytes[24..28].try_into().ok()?);
    let channels = u16::from_le_bytes(bytes[22..24].try_into().ok()?) as u32;
    let bits = u16::from_le_bytes(bytes[34..36].try_into().ok()?) as u32;
    let data = u32::from_le_bytes(bytes[40..44].try_into().ok()?);
    let frame = (channels * bits / 8).max(1);
    Some((rate, data / frame))
}

fn video_note(bytes: &[u8], ext: &str) -> (String, usize, usize) {
    (
        format!(
            "video {ext} bytes {} header {}",
            bytes.len(),
            video_brand(bytes)
        ),
        0,
        bytes.len(),
    )
}

fn video_brand(bytes: &[u8]) -> &str {
    if bytes.len() >= 12 && &bytes[4..8] == b"ftyp" {
        "mp4"
    } else if bytes.starts_with(b"\x1a\x45\xdf\xa3") {
        "webm"
    } else if bytes.starts_with(b"RIFF") {
        "avi"
    } else {
        "unknown"
    }
}

fn ext_of(path: &str) -> &str {
    path.rsplit(['/', '\\'])
        .next()
        .and_then(|name| name.rsplit_once('.'))
        .map(|(_, ext)| ext)
        .unwrap_or("")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn png_ihdr_size_is_recorded() {
        // 1x1 PNG. The IHDR width/height are the bytes after the signature.
        let png = png_1x1();
        let chunks = chunk("dot.png", &png);
        assert_eq!(chunks.len(), 1);
        assert!(chunks[0].content.contains("1x1"), "{}", chunks[0].content);
        assert_eq!(chunks[0].modality, "image");
    }

    fn png_1x1() -> Vec<u8> {
        let mut bytes = Vec::new();
        bytes.extend_from_slice(b"\x89PNG\r\n\x1a\n");
        bytes.extend_from_slice(&13u32.to_be_bytes());
        bytes.extend_from_slice(b"IHDR");
        bytes.extend_from_slice(&1u32.to_be_bytes());
        bytes.extend_from_slice(&1u32.to_be_bytes());
        bytes.extend_from_slice(&[8, 2, 0, 0, 0]);
        bytes.extend_from_slice(&0u32.to_be_bytes());
        bytes
    }
}
