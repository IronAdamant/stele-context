//! Language dispatch. Python and JSON have grammars; every other extension
//! is one opaque span so the bytes still round-trip.

mod json;
pub mod kind;
mod python;
pub(crate) mod python_symbols;

use crate::syntax::cst::{Builder, Tree};
use crate::syntax::query::SymbolHit;

pub fn parse(extension: &str, source: &str) -> Tree {
    parse_with_symbols(extension, source).0
}

pub fn parse_with_symbols(extension: &str, source: &str) -> (Tree, Vec<SymbolHit>) {
    match extension {
        "py" | "pyw" | "pyx" => python::parse(source),
        "json" => (json::parse(source), Vec::new()),
        _ => (opaque(source), Vec::new()),
    }
}

fn opaque(source: &str) -> Tree {
    let mut builder = Builder::new();
    builder.start(kind::FILE, 0);
    builder.leaf(kind::STRING, 0, source.len() as u32);
    builder.finish(source.len() as u32);
    builder.tree()
}
