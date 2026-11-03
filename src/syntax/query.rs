//! Symbol query over a tree. Languages fill `SymbolHit` while parsing;
//! this module is the stable shape the graph will read.

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SymbolHit {
    pub name: String,
    pub kind: &'static str,
    pub role: &'static str,
    pub line: u32,
    pub container: Option<String>,
}

pub fn symbols_of(hits: &[SymbolHit]) -> Vec<SymbolHit> {
    hits.to_vec()
}
