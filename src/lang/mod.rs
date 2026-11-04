//! Language dispatch. Python and JSON keep their grammars. Every other
//! extension uses the shared lossless scanner.

mod json;
pub mod kind;
pub(crate) mod python;
pub(crate) mod python_symbols;
mod scan;

use crate::syntax::cst::Tree;
use crate::syntax::query::SymbolHit;

pub fn parse(extension: &str, source: &str) -> Tree {
    parse_with_symbols(extension, source).0
}

pub fn parse_with_symbols(extension: &str, source: &str) -> (Tree, Vec<SymbolHit>) {
    match extension {
        "py" | "pyw" | "pyx" => python::parse(source),
        "json" => (json::parse(source), Vec::new()),
        other => (
            scan::parse(source),
            crate::symbols::extract(other, source)
                .into_iter()
                .map(|hit| SymbolHit {
                    name: hit.name,
                    kind: static_label(&hit.kind),
                    role: static_label(&hit.role),
                    line: hit.line,
                    container: hit.container,
                })
                .collect(),
        ),
    }
}

fn static_label(text: &str) -> &'static str {
    match text {
        "function" => "function",
        "class" => "class",
        "variable" => "variable",
        "module" => "module",
        "import" => "import",
        "css_class" => "css_class",
        "css_id" => "css_id",
        "definition" => "definition",
        "reference" => "reference",
        "method" => "method",
        "type" => "type",
        "constant" => "constant",
        "field" => "field",
        _ => "symbol",
    }
}
