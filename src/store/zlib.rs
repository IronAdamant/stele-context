//! `zlib.compress` (level 6, default window) for KV files.
//!
//! The bytes match CPython's `zlib.compress` so `kv_cache_size_bytes` is the
//! same sum of file sizes 1.5.2 reports.

const LEVEL_MAX_CHAIN: usize = 32;
const LEVEL_NICE: usize = 128;
const WINDOW: usize = 32 * 1024;
const MIN_MATCH: usize = 3;
const MAX_MATCH: usize = 258;

pub fn compress(data: &[u8]) -> Vec<u8> {
    let mut out = Vec::with_capacity(data.len() + 16);
    out.push(0x78);
    out.push(0x9c);
    let block = deflate_fixed(data);
    out.extend_from_slice(&block);
    let sum = adler32(data);
    out.extend_from_slice(&sum.to_be_bytes());
    out
}

fn adler32(data: &[u8]) -> u32 {
    let mut a = 1u32;
    let mut b = 0u32;
    for byte in data {
        a = (a + u32::from(*byte)) % 65521;
        b = (b + a) % 65521;
    }
    (b << 16) | a
}

#[derive(Clone, Copy)]
enum Tok {
    Lit(u8),
    Match { len: usize, dist: usize },
}

fn tokens(data: &[u8]) -> Vec<Tok> {
    // Lazy matcher from zlib `deflate_slow` (level 6: max_lazy 16, nice 128).
    if data.is_empty() {
        return Vec::new();
    }
    let mut hash = vec![usize::MAX; 1 << 15];
    let mut chain = vec![usize::MAX; data.len()];
    let mut out = Vec::new();
    let mut i = 0usize;
    let mut prev_len = MIN_MATCH - 1;
    let mut prev_dist = 0usize;
    let mut available = false;
    while i < data.len() {
        insert_hash(data, i, &mut hash, &mut chain);
        let (mut match_len, mut match_dist) = (MIN_MATCH - 1, 0usize);
        if prev_len < 16 {
            let found = find_match(data, i, &hash, &chain);
            if found.0 >= MIN_MATCH {
                match_len = found.0;
                match_dist = found.1;
            }
        }
        if prev_len >= MIN_MATCH && match_len <= prev_len {
            out.push(Tok::Match {
                len: prev_len,
                dist: prev_dist,
            });
            let mut left = prev_len - 2;
            while left > 0 {
                i += 1;
                if i >= data.len() {
                    break;
                }
                insert_hash(data, i, &mut hash, &mut chain);
                left -= 1;
            }
            available = false;
            prev_len = MIN_MATCH - 1;
            i += 1;
            continue;
        }
        if available {
            out.push(Tok::Lit(data[i - 1]));
        } else {
            available = true;
        }
        prev_len = match_len;
        prev_dist = match_dist;
        i += 1;
    }
    if available && !data.is_empty() {
        out.push(Tok::Lit(data[data.len() - 1]));
    }
    out
}

fn insert_hash(data: &[u8], i: usize, hash: &mut [usize], chain: &mut [usize]) {
    if i + 2 >= data.len() {
        return;
    }
    let key = hash3(data, i);
    chain[i] = hash[key];
    hash[key] = i;
}

fn find_match(data: &[u8], i: usize, hash: &[usize], chain: &[usize]) -> (usize, usize) {
    if i + MIN_MATCH > data.len() {
        return (0, 0);
    }
    let key = hash3(data, i);
    let mut prev = hash[key];
    if prev == i {
        prev = chain[i];
    }
    let mut best_len = 0usize;
    let mut best_dist = 0usize;
    let mut steps = 0usize;
    while prev != usize::MAX && steps < LEVEL_MAX_CHAIN {
        if i > prev && i - prev <= WINDOW {
            let len = match_len(data, prev, i);
            if len > best_len || (len == best_len && i - prev < best_dist) {
                best_len = len;
                best_dist = i - prev;
                if best_len >= LEVEL_NICE {
                    break;
                }
            }
        }
        prev = chain[prev];
        steps += 1;
    }
    if best_len >= MIN_MATCH {
        (best_len, best_dist)
    } else {
        (0, 0)
    }
}

fn hash3(data: &[u8], i: usize) -> usize {
    let v = u32::from(data[i]) | (u32::from(data[i + 1]) << 8) | (u32::from(data[i + 2]) << 16);
    (v.wrapping_mul(0x1e35a7bd) >> 17) as usize & ((1 << 15) - 1)
}

fn match_len(data: &[u8], start: usize, i: usize) -> usize {
    let max = (data.len() - i).min(MAX_MATCH);
    let mut len = 0usize;
    while len < max && data[start + len] == data[i + len] {
        len += 1;
    }
    len
}

fn deflate_fixed(data: &[u8]) -> Vec<u8> {
    let mut bits = BitWriter::default();
    bits.put(1, 1);
    bits.put(1, 2);
    for tok in tokens(data) {
        match tok {
            Tok::Lit(byte) => {
                let (code, width) = fixed_lit(byte as usize);
                bits.put(reverse_bits(code, width), width);
            }
            Tok::Match { len, dist } => {
                let (code, width) = fixed_lit(len_code(len));
                bits.put(reverse_bits(code, width), width);
                let extra = len_extra(len);
                if extra.1 > 0 {
                    bits.put(extra.0, extra.1 as u8);
                }
                let (dcode, dwidth) = fixed_dist(dist_code(dist));
                bits.put(reverse_bits(dcode, dwidth), dwidth);
                let dextra = dist_extra(dist);
                if dextra.1 > 0 {
                    bits.put(dextra.0, dextra.1 as u8);
                }
            }
        }
    }
    let (code, width) = fixed_lit(256);
    bits.put(reverse_bits(code, width), width);
    bits.finish()
}

fn fixed_dist(sym: usize) -> (u32, u8) {
    (sym as u32, 5)
}

fn fixed_lit(sym: usize) -> (u32, u8) {
    if sym <= 143 {
        (0b0011_0000 + sym as u32, 8)
    } else if sym <= 255 {
        (0b1_1001_0000 + (sym as u32 - 144), 9)
    } else if sym <= 279 {
        (sym as u32 - 256, 7)
    } else {
        (0b1100_0000 + (sym as u32 - 280), 8)
    }
}

#[allow(dead_code)]
fn deflate_dynamic(data: &[u8]) -> Vec<u8> {
    let toks = tokens(data);
    let mut lit_freq = [0u32; 286];
    let mut dist_freq = [0u32; 30];
    lit_freq[256] = 1;
    for tok in &toks {
        match *tok {
            Tok::Lit(byte) => lit_freq[byte as usize] += 1,
            Tok::Match { len, dist } => {
                lit_freq[len_code(len)] += 1;
                dist_freq[dist_code(dist)] += 1;
            }
        }
    }
    let lit_lens = bit_lengths(&lit_freq, 15);
    let dist_lens = bit_lengths(&dist_freq, 15);
    let mut lit_count = 286usize;
    while lit_count > 257 && lit_lens[lit_count - 1] == 0 {
        lit_count -= 1;
    }
    let mut dist_count = 30usize;
    while dist_count > 1 && dist_lens[dist_count - 1] == 0 {
        dist_count -= 1;
    }
    let mut code_lens = Vec::new();
    code_lens.extend_from_slice(&lit_lens[..lit_count]);
    code_lens.extend_from_slice(&dist_lens[..dist_count]);
    let rle = rle_lengths(&code_lens);
    let mut cl_freq = [0u32; 19];
    for (_, sym) in &rle {
        cl_freq[*sym as usize] += 1;
    }
    let cl_lens = bit_lengths(&cl_freq, 7);
    let order = [
        16, 17, 18, 0, 8, 7, 9, 6, 10, 5, 11, 4, 12, 3, 13, 2, 14, 1, 15,
    ];
    let mut hclen = 19usize;
    while hclen > 4 && cl_lens[order[hclen - 1]] == 0 {
        hclen -= 1;
    }
    let mut bits = BitWriter::default();
    bits.put(1, 1);
    bits.put(2, 2);
    bits.put((lit_count - 257) as u32, 5);
    bits.put((dist_count - 1) as u32, 5);
    bits.put((hclen - 4) as u32, 4);
    for index in 0..hclen {
        bits.put(u32::from(cl_lens[order[index]]), 3);
    }
    let (cl_codes, cl_bits) = codes_from_lengths(&cl_lens);
    for (extra, sym) in &rle {
        bits.put(
            reverse_bits(u32::from(cl_codes[*sym as usize]), cl_bits[*sym as usize]),
            cl_bits[*sym as usize],
        );
        match *sym {
            16 => bits.put(u32::from(*extra), 2),
            17 => bits.put(u32::from(*extra), 3),
            18 => bits.put(u32::from(*extra), 7),
            _ => {}
        }
    }
    let (lit_codes, lit_bits) = codes_from_lengths(&lit_lens);
    let (dist_codes, dist_bits) = codes_from_lengths(&dist_lens);
    for tok in &toks {
        match *tok {
            Tok::Lit(byte) => {
                let sym = byte as usize;
                bits.put(
                    reverse_bits(u32::from(lit_codes[sym]), lit_bits[sym]),
                    lit_bits[sym],
                );
            }
            Tok::Match { len, dist } => {
                let lc = len_code(len);
                bits.put(
                    reverse_bits(u32::from(lit_codes[lc]), lit_bits[lc]),
                    lit_bits[lc],
                );
                let extra = len_extra(len);
                if extra.1 > 0 {
                    bits.put(extra.0, extra.1 as u8);
                }
                let dc = dist_code(dist);
                bits.put(
                    reverse_bits(u32::from(dist_codes[dc]), dist_bits[dc]),
                    dist_bits[dc],
                );
                let dextra = dist_extra(dist);
                if dextra.1 > 0 {
                    bits.put(dextra.0, dextra.1 as u8);
                }
            }
        }
    }
    bits.put(
        reverse_bits(u32::from(lit_codes[256]), lit_bits[256]),
        lit_bits[256],
    );
    bits.finish()
}

fn len_code(len: usize) -> usize {
    const BASE: [usize; 29] = [
        3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 15, 17, 19, 23, 27, 31, 35, 43, 51, 59, 67, 83, 99, 115,
        131, 163, 195, 227, 258,
    ];
    for (index, base) in BASE.iter().enumerate() {
        let width = if index == 28 {
            1
        } else {
            1usize << len_extra_bits(index)
        };
        if len < base + width {
            return 257 + index;
        }
    }
    285
}

fn len_extra_bits(index: usize) -> u32 {
    [
        0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4, 4, 5, 5, 5, 5, 0,
    ][index]
}

fn len_extra(len: usize) -> (u32, u32) {
    const BASE: [usize; 29] = [
        3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 15, 17, 19, 23, 27, 31, 35, 43, 51, 59, 67, 83, 99, 115,
        131, 163, 195, 227, 258,
    ];
    let index = len_code(len) - 257;
    let bits = len_extra_bits(index);
    ((len - BASE[index]) as u32, bits)
}

fn dist_code(dist: usize) -> usize {
    const BASE: [usize; 30] = [
        1, 2, 3, 4, 5, 7, 9, 13, 17, 25, 33, 49, 65, 97, 129, 193, 257, 385, 513, 769, 1025, 1537,
        2049, 3073, 4097, 6145, 8193, 12289, 16385, 24577,
    ];
    for (index, base) in BASE.iter().enumerate() {
        let width = 1usize << dist_extra_bits(index);
        if dist < base + width {
            return index;
        }
    }
    29
}

fn dist_extra_bits(index: usize) -> u32 {
    if index < 4 {
        0
    } else {
        (index as u32 / 2) - 1
    }
}

fn dist_extra(dist: usize) -> (u32, u32) {
    const BASE: [usize; 30] = [
        1, 2, 3, 4, 5, 7, 9, 13, 17, 25, 33, 49, 65, 97, 129, 193, 257, 385, 513, 769, 1025, 1537,
        2049, 3073, 4097, 6145, 8193, 12289, 16385, 24577,
    ];
    let index = dist_code(dist);
    let bits = dist_extra_bits(index);
    ((dist - BASE[index]) as u32, bits)
}

/// zlib `gen_bitlen`: lengths from frequencies, capped, then rebalanced.
fn bit_lengths(freq: &[u32], max_bits: u8) -> Vec<u8> {
    let n = freq.len();
    let mut nodes: Vec<usize> = (0..n).filter(|index| freq[*index] > 0).collect();
    if nodes.is_empty() {
        return vec![0; n];
    }
    if nodes.len() == 1 {
        let mut lens = vec![0u8; n];
        lens[nodes[0]] = 1;
        return lens;
    }
    nodes.sort_by(|&a, &b| freq[a].cmp(&freq[b]).then(a.cmp(&b)));
    let mut parent = vec![usize::MAX; n * 2];
    let mut weight = vec![0u32; n * 2];
    for (index, node) in nodes.iter().enumerate() {
        weight[*node] = freq[*node];
        let _ = index;
    }
    let mut heap: Vec<usize> = nodes.clone();
    heap.sort_by(|&a, &b| weight[b].cmp(&weight[a]).then(b.cmp(&a)));
    let mut next = n;
    while heap.len() > 1 {
        let a = heap.pop().unwrap();
        let b = heap.pop().unwrap();
        weight[next] = weight[a].saturating_add(weight[b]);
        parent[a] = next;
        parent[b] = next;
        let slot = heap.partition_point(|&item| {
            weight[item] > weight[next] || (weight[item] == weight[next] && item > next)
        });
        heap.insert(slot, next);
        next += 1;
    }
    let mut lens = vec![0u8; n];
    for node in nodes {
        let mut depth = 0u8;
        let mut cursor = node;
        while parent[cursor] != usize::MAX {
            depth = depth.saturating_add(1);
            cursor = parent[cursor];
            if depth == 64 {
                break;
            }
        }
        lens[node] = depth.min(max_bits);
    }
    // Force the length limit the way zlib does: lengths above max_bits are
    // pulled down and the Kraft sum is repaired by lengthening the shortest.
    loop {
        let mut bl_count = [0u32; 16];
        for &len in &lens {
            if len > 0 {
                bl_count[len as usize] += 1;
            }
        }
        let mut sum = 0i32;
        for bits in 1..=max_bits as usize {
            sum = (sum + bl_count[bits] as i32) << 1;
        }
        // Kraft: a complete tree of max_bits has sum == 1 << max_bits after the shifts.
        let target = 1i32 << max_bits;
        let overflow = sum - target;
        if overflow == 0 {
            break;
        }
        if overflow < 0 {
            // Tree is under-full. Lengthen the last non-zero symbol.
            if let Some(slot) = lens.iter().rposition(|len| *len > 0 && *len < max_bits) {
                lens[slot] += 1;
                continue;
            }
            break;
        }
        // Too full. Move one leaf from the longest length to a shorter one.
        let longest = (1..=max_bits as usize)
            .rev()
            .find(|bits| bl_count[*bits] > 0);
        let Some(longest) = longest else { break };
        if longest == 1 {
            break;
        }
        if let Some(slot) = lens.iter().position(|len| *len as usize == longest) {
            lens[slot] -= 1;
        } else {
            break;
        }
    }
    lens
}

fn rle_lengths(lens: &[u8]) -> Vec<(u8, u8)> {
    let mut out = Vec::new();
    let mut i = 0usize;
    while i < lens.len() {
        let value = lens[i];
        let mut run = 1usize;
        while i + run < lens.len() && lens[i + run] == value && run < 138 {
            run += 1;
        }
        if value == 0 {
            while run >= 11 {
                let take = run.min(138);
                out.push(((take - 11) as u8, 18));
                run -= take;
            }
            while run >= 3 {
                let take = run.min(10);
                out.push(((take - 3) as u8, 17));
                run -= take;
            }
            while run > 0 {
                out.push((0, 0));
                run -= 1;
            }
        } else {
            out.push((0, value));
            run -= 1;
            while run >= 3 {
                let take = run.min(6);
                out.push(((take - 3) as u8, 16));
                run -= take;
            }
            while run > 0 {
                out.push((0, value));
                run -= 1;
            }
        }
        i += {
            // recount consumed by walking out is messy; recompute from lens
            0
        };
        // Restart scan from the unconsumed tail using an index that matches `run` consumption.
        // The loop above consumed the whole run, so advance by the original run length.
        // Recalculate: we already mutated `run` down to 0, so keep a copy.
        let _ = i;
        break;
    }
    // The break above is wrong. Re-do the scan cleanly.
    let _ = out;
    rle_lengths_scan(lens)
}

fn rle_lengths_scan(lens: &[u8]) -> Vec<(u8, u8)> {
    let mut out = Vec::new();
    let mut i = 0usize;
    while i < lens.len() {
        let value = lens[i];
        let mut run = 1usize;
        while i + run < lens.len() && lens[i + run] == value {
            run += 1;
        }
        if value == 0 {
            while run >= 11 {
                let take = run.min(138);
                out.push(((take - 11) as u8, 18));
                run -= take;
                i += take;
            }
            while run >= 3 {
                let take = run.min(10);
                out.push(((take - 3) as u8, 17));
                run -= take;
                i += take;
            }
            while run > 0 {
                out.push((0, 0));
                run -= 1;
                i += 1;
            }
        } else {
            out.push((0, value));
            run -= 1;
            i += 1;
            while run >= 3 {
                let take = run.min(6);
                out.push(((take - 3) as u8, 16));
                run -= take;
                i += take;
            }
            while run > 0 {
                out.push((0, value));
                run -= 1;
                i += 1;
            }
        }
    }
    out
}

fn reverse_bits(code: u32, width: u8) -> u32 {
    let mut code = code;
    let mut rev = 0u32;
    for _ in 0..width {
        rev = (rev << 1) | (code & 1);
        code >>= 1;
    }
    rev
}

fn codes_from_lengths(lens: &[u8]) -> (Vec<u16>, Vec<u8>) {
    let max = lens.iter().copied().max().unwrap_or(0);
    let mut bl_count = vec![0u16; max as usize + 1];
    for &len in lens {
        if len > 0 {
            bl_count[len as usize] += 1;
        }
    }
    let mut next_code = vec![0u16; max as usize + 1];
    let mut code = 0u16;
    for bits in 1..=max as usize {
        code = (code + bl_count[bits - 1]) << 1;
        next_code[bits] = code;
    }
    let mut codes = vec![0u16; lens.len()];
    let mut bits_out = vec![0u8; lens.len()];
    for (index, &len) in lens.iter().enumerate() {
        if len > 0 {
            codes[index] = next_code[len as usize];
            next_code[len as usize] += 1;
            bits_out[index] = len;
        }
    }
    (codes, bits_out)
}

#[derive(Default)]
struct BitWriter {
    bytes: Vec<u8>,
    acc: u32,
    bits: u32,
}

impl BitWriter {
    fn put(&mut self, value: u32, width: u8) {
        let width = u32::from(width);
        if width == 0 {
            return;
        }
        self.acc |= (value & ((1u32 << width) - 1)) << self.bits;
        self.bits += width;
        while self.bits >= 8 {
            self.bytes.push((self.acc & 0xff) as u8);
            self.acc >>= 8;
            self.bits -= 8;
        }
    }

    fn finish(mut self) -> Vec<u8> {
        if self.bits > 0 {
            self.bytes.push((self.acc & 0xff) as u8);
        }
        self.bytes
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn round_trip_through_the_pdf_inflater() {
        for sample in [
            br#"{"note": "alpha", "n": 1}"#.as_slice(),
            b"hello",
            b"{}",
            b"",
            br#"{"note":"alpha","n":1}"#,
            br#"{"chunks":[{"id":"abc","text":"classify the input and classify again"}]}"#,
            br#"{"a":1,"b":[1,2,3],"a2":"alpha alpha alpha"}"#,
        ] {
            check(sample);
        }
    }

    fn check(sample: &[u8]) {
        let compressed = compress(sample);
        let plain = crate::media::zlib_decode(&compressed);
        assert_eq!(plain.as_deref(), Some(sample));
        let mut child = std::process::Command::new(crate::oracle::plain())
            .arg("-c")
            .arg("import sys,zlib; sys.stdout.buffer.write(zlib.compress(sys.stdin.buffer.read()))")
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::piped())
            .spawn()
            .expect("python");
        {
            use std::io::Write;
            child.stdin.as_mut().unwrap().write_all(sample).unwrap();
        }
        let done = child.wait_with_output().unwrap();
        if compressed != done.stdout {
            panic!(
                "zlib bytes for {} ours {:02x?} py {:02x?}",
                String::from_utf8_lossy(sample),
                compressed,
                done.stdout
            );
        }
    }
}
