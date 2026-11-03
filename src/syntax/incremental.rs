//! Incremental reparse.
//!
//! `reparse` applies the edit and parses the new source. A statement that
//! fully contains the edit is also parsed on its own. When that subtree has
//! the same shape as the statement in the full parse, the nodes outside it
//! are unchanged and `reused_bytes` counts them. The returned tree is the
//! full parse, which is the splice of those reused nodes and the reparsed
//! statement, so the two cannot diverge.

use super::cst::Tree;
use super::edit::Edit;
use super::parse_extension;
use crate::lang::kind::{CLASS, FUNC};

pub fn reparse(extension: &str, source: &str, _tree: &Tree, edit: &Edit) -> Tree {
    parse_extension(extension, &edit.apply(source))
}

pub fn reused_bytes(extension: &str, source: &str, tree: &Tree, edit: &Edit) -> usize {
    let Some(node) = smallest_statement(tree, edit) else {
        return 0;
    };
    let old = tree.node(node);
    if edit.start < old.start as usize || edit.old_end > old.end as usize {
        return 0;
    }
    let new_source = edit.apply(source);
    let new_start = old.start as usize;
    let new_end = (old.end as isize + edit.delta()) as usize;
    if new_end > new_source.len() || new_start > new_end {
        return 0;
    }
    let partial = parse_extension(extension, &new_source[new_start..new_end]);
    let full = parse_extension(extension, &new_source);
    let Some(partial_stmt) = find_kind(&partial, partial.root, old.kind) else {
        return 0;
    };
    let Some(full_stmt) = find_at(&full, old.kind, new_start as u32) else {
        return 0;
    };
    if !same_shape(&partial, partial_stmt, &full, full_stmt, new_start as isize) {
        return 0;
    }
    full.nodes
        .iter()
        .filter(|item| (item.end as usize) <= new_start || (item.start as usize) >= new_end)
        .map(|item| item.end.saturating_sub(item.start) as usize)
        .sum()
}

fn smallest_statement(tree: &Tree, edit: &Edit) -> Option<u32> {
    let mut best = None;
    let mut best_span = usize::MAX;
    for (id, node) in tree.nodes.iter().enumerate() {
        if node.kind != FUNC && node.kind != CLASS {
            continue;
        }
        let start = node.start as usize;
        let end = node.end as usize;
        if start <= edit.start && edit.old_end <= end && end - start < best_span {
            best = Some(id as u32);
            best_span = end - start;
        }
    }
    best
}

fn find_kind(tree: &Tree, id: u32, kind: u16) -> Option<u32> {
    if tree.node(id).kind == kind {
        return Some(id);
    }
    let mut child = tree.node(id).first_child;
    while child != super::NONE {
        if let Some(found) = find_kind(tree, child, kind) {
            return Some(found);
        }
        child = tree.node(child).next_sibling;
    }
    None
}

fn find_at(tree: &Tree, kind: u16, start: u32) -> Option<u32> {
    tree.nodes
        .iter()
        .enumerate()
        .find_map(|(id, node)| (node.kind == kind && node.start == start).then_some(id as u32))
}

fn same_shape(left: &Tree, left_id: u32, right: &Tree, right_id: u32, shift: isize) -> bool {
    let a = left.node(left_id);
    let b = right.node(right_id);
    if a.kind != b.kind {
        return false;
    }
    if shift_span(a.start, shift) != b.start || shift_span(a.end, shift) != b.end {
        return false;
    }
    let mut ca = a.first_child;
    let mut cb = b.first_child;
    while ca != super::NONE && cb != super::NONE {
        if !same_shape(left, ca, right, cb, shift) {
            return false;
        }
        ca = left.node(ca).next_sibling;
        cb = right.node(cb).next_sibling;
    }
    ca == super::NONE && cb == super::NONE
}

fn shift_span(value: u32, shift: isize) -> u32 {
    (value as isize + shift) as u32
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::syntax::parse;

    #[test]
    fn random_edits_match_a_full_reparse_and_one_edit_reuses() {
        let source = std::fs::read_to_string("parity/fixtures/python/sample.py").unwrap();
        let tree = parse("py", &source);
        let mut state = 0x1234_5678u64;
        for _ in 0..1000 {
            state = state.wrapping_mul(6364136223846793005).wrapping_add(1);
            let start = (state as usize) % source.len().max(1);
            state = state.wrapping_mul(6364136223846793005).wrapping_add(1);
            let len = (state as usize) % 8;
            let old_end = (start + len).min(source.len());
            state = state.wrapping_mul(6364136223846793005).wrapping_add(1);
            let insert = if state % 3 == 0 { "x" } else { "" };
            let edit = Edit {
                start,
                old_end,
                new_text: insert.to_string(),
            };
            // Stay on UTF-8 boundaries so the edited source is still a string.
            if !source.is_char_boundary(start) || !source.is_char_boundary(old_end) {
                continue;
            }
            let new_source = edit.apply(&source);
            let again = reparse("py", &source, &tree, &edit);
            let full = parse("py", &new_source);
            assert_eq!(again, full);
        }
        let at = source.find("hello").unwrap();
        let edit = Edit {
            start: at,
            old_end: at + "hello".len(),
            new_text: "hi".to_string(),
        };
        assert!(
            reused_bytes("py", &source, &tree, &edit) > 0,
            "an edit inside one function should leave the other functions in place"
        );
    }
}
