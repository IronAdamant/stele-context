//! zlib/deflate (RFC 1950/1951) for PDF FlateDecode. No external crate.

pub fn zlib_decode(data: &[u8]) -> Option<Vec<u8>> {
    if data.len() < 6 {
        return None;
    }
    // CMF/FLG. PDF FlateDecode uses a zlib wrapper.
    let cm = data[0] & 0x0f;
    if cm != 8 {
        return None;
    }
    inflate(&data[2..data.len().saturating_sub(4)])
}

pub fn inflate(data: &[u8]) -> Option<Vec<u8>> {
    let mut bits = Bits { data, pos: 0 };
    let mut out = Vec::new();
    loop {
        let bfinal = bits.take(1)?;
        let btype = bits.take(2)?;
        match btype {
            0 => stored(&mut bits, &mut out)?,
            1 => {
                let lit = fixed_lit();
                let dist = fixed_dist();
                huffman_block(&mut bits, &lit, &dist, &mut out)?;
            }
            2 => {
                let (lit, dist) = dynamic(&mut bits)?;
                huffman_block(&mut bits, &lit, &dist, &mut out)?;
            }
            _ => return None,
        }
        if bfinal == 1 {
            break;
        }
    }
    Some(out)
}

struct Bits<'a> {
    data: &'a [u8],
    pos: usize,
}

impl Bits<'_> {
    fn take(&mut self, n: u32) -> Option<u32> {
        let mut value = 0u32;
        for i in 0..n {
            let byte = *self.data.get(self.pos / 8)?;
            let bit = (byte >> (self.pos % 8)) & 1;
            value |= u32::from(bit) << i;
            self.pos += 1;
        }
        Some(value)
    }

    fn align(&mut self) {
        self.pos = (self.pos + 7) & !7;
    }
}

struct Huff {
    // (code, bit length, symbol), codes compared MSB-first via a tree.
    tree: Vec<i32>,
}

impl Huff {
    fn decode(&self, bits: &mut Bits) -> Option<u32> {
        let mut node = 0i32;
        for _ in 0..16 {
            let bit = bits.take(1)?;
            let slot = (node as usize) * 2 + bit as usize;
            let next = *self.tree.get(slot)?;
            if next < 0 {
                return Some((-next - 1) as u32);
            }
            node = next;
        }
        None
    }
}

fn from_lengths(lengths: &[u8]) -> Huff {
    let mut bl_count = [0u32; 16];
    for &len in lengths {
        if len > 0 {
            bl_count[len as usize] += 1;
        }
    }
    let mut next_code = [0u32; 16];
    let mut code = 0u32;
    for bits in 1..16 {
        code = (code + bl_count[bits - 1]) << 1;
        next_code[bits] = code;
    }
    // Tree nodes: even index left (bit 0), odd right (bit 1). Negative = symbol+1.
    let mut tree = vec![0i32; 2];
    for (symbol, &len) in lengths.iter().enumerate() {
        if len == 0 {
            continue;
        }
        let assigned = next_code[len as usize];
        next_code[len as usize] += 1;
        let mut node = 0usize;
        for shift in (0..len).rev() {
            let bit = ((assigned >> shift) & 1) as usize;
            let slot = node * 2 + bit;
            if shift == 0 {
                while tree.len() <= slot {
                    tree.push(0);
                }
                tree[slot] = -((symbol as i32) + 1);
            } else {
                while tree.len() <= slot + 2 {
                    tree.push(0);
                }
                if tree[slot] <= 0 {
                    let fresh = tree.len() / 2;
                    tree[slot] = fresh as i32;
                    tree.push(0);
                    tree.push(0);
                }
                node = tree[slot] as usize;
            }
        }
    }
    Huff { tree }
}

fn fixed_lit() -> Huff {
    let mut lengths = vec![0u8; 288];
    for (i, len) in lengths.iter_mut().enumerate() {
        *len = match i {
            0..=143 => 8,
            144..=255 => 9,
            256..=279 => 7,
            _ => 8,
        };
    }
    from_lengths(&lengths)
}

fn fixed_dist() -> Huff {
    from_lengths(&[5u8; 32])
}

fn dynamic(bits: &mut Bits) -> Option<(Huff, Huff)> {
    let hlit = bits.take(5)? as usize + 257;
    let hdist = bits.take(5)? as usize + 1;
    let hclen = bits.take(4)? as usize + 4;
    const ORDER: [usize; 19] = [
        16, 17, 18, 0, 8, 7, 9, 6, 10, 5, 11, 4, 12, 3, 13, 2, 14, 1, 15,
    ];
    let mut cl_len = vec![0u8; 19];
    for i in 0..hclen {
        cl_len[ORDER[i]] = bits.take(3)? as u8;
    }
    let cl = from_lengths(&cl_len);
    let mut lengths = vec![0u8; hlit + hdist];
    let mut index = 0usize;
    while index < lengths.len() {
        let symbol = cl.decode(bits)? as usize;
        match symbol {
            0..=15 => {
                lengths[index] = symbol as u8;
                index += 1;
            }
            16 => {
                let extra = bits.take(2)? as usize + 3;
                let prev = *lengths.get(index.checked_sub(1)?)?;
                for _ in 0..extra {
                    lengths[index] = prev;
                    index += 1;
                }
            }
            17 => {
                let extra = bits.take(3)? as usize + 3;
                index += extra;
            }
            18 => {
                let extra = bits.take(7)? as usize + 11;
                index += extra;
            }
            _ => return None,
        }
    }
    Some((
        from_lengths(&lengths[..hlit]),
        from_lengths(&lengths[hlit..]),
    ))
}

fn stored(bits: &mut Bits, out: &mut Vec<u8>) -> Option<()> {
    bits.align();
    let len = bits.take(16)? as usize;
    let nlen = bits.take(16)? as usize;
    if len != (!nlen & 0xffff) {
        return None;
    }
    let start = bits.pos / 8;
    let end = start.checked_add(len)?;
    out.extend_from_slice(bits.data.get(start..end)?);
    bits.pos = end * 8;
    Some(())
}

fn huffman_block(bits: &mut Bits, lit: &Huff, dist: &Huff, out: &mut Vec<u8>) -> Option<()> {
    const LEN_BASE: [usize; 29] = [
        3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 15, 17, 19, 23, 27, 31, 35, 43, 51, 59, 67, 83, 99, 115,
        131, 163, 195, 227, 258,
    ];
    const LEN_EXTRA: [u32; 29] = [
        0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4, 4, 5, 5, 5, 5, 0,
    ];
    const DIST_BASE: [usize; 30] = [
        1, 2, 3, 4, 5, 7, 9, 13, 17, 25, 33, 49, 65, 97, 129, 193, 257, 385, 513, 769, 1025, 1537,
        2049, 3073, 4097, 6145, 8193, 12289, 16385, 24577,
    ];
    const DIST_EXTRA: [u32; 30] = [
        0, 0, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7, 8, 8, 9, 9, 10, 10, 11, 11, 12, 12,
        13, 13,
    ];
    loop {
        let symbol = lit.decode(bits)?;
        match symbol {
            0..=255 => out.push(symbol as u8),
            256 => return Some(()),
            257..=285 => {
                let index = (symbol - 257) as usize;
                let length = LEN_BASE[index] + bits.take(LEN_EXTRA[index])? as usize;
                let distance_symbol = dist.decode(bits)? as usize;
                let distance =
                    DIST_BASE[distance_symbol] + bits.take(DIST_EXTRA[distance_symbol])? as usize;
                if distance == 0 || distance > out.len() {
                    return None;
                }
                for _ in 0..length {
                    let byte = out[out.len() - distance];
                    out.push(byte);
                }
            }
            _ => return None,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn zlib_hello_pdf_page() {
        let data = hex_bytes("789cf348cdc9c9570870715328484c4f050022ff04ac");
        let text = zlib_decode(&data).unwrap();
        assert_eq!(text, b"Hello PDF page");
    }

    fn hex_bytes(text: &str) -> Vec<u8> {
        (0..text.len())
            .step_by(2)
            .map(|index| u8::from_str_radix(&text[index..index + 2], 16).unwrap())
            .collect()
    }
}
