//! Symbol edges, ported from `resolve_symbols` and `_resolve_test_edges`.
//!
//! An edge means the source chunk references a symbol defined in the target
//! chunk. `impact_radius`, `coupling`, and search `edges` read this table.

use crate::store::{Edge, Symbol};
use std::collections::{HashMap, HashSet};

const NOISE: &[&str] = &[
    "Array",
    "AttributeError",
    "Boolean",
    "Date",
    "Error",
    "Exception",
    "FileNotFoundError",
    "ImportError",
    "IndexError",
    "JSON",
    "KeyError",
    "Map",
    "Math",
    "NotImplementedError",
    "Number",
    "OSError",
    "Object",
    "Promise",
    "RegExp",
    "RuntimeError",
    "Set",
    "StopIteration",
    "String",
    "TypeError",
    "ValueError",
    "__add__",
    "__bool__",
    "__call__",
    "__contains__",
    "__del__",
    "__delitem__",
    "__enter__",
    "__eq__",
    "__exit__",
    "__ge__",
    "__getattr__",
    "__getitem__",
    "__gt__",
    "__hash__",
    "__init__",
    "__iter__",
    "__le__",
    "__len__",
    "__lt__",
    "__mul__",
    "__ne__",
    "__new__",
    "__next__",
    "__repr__",
    "__setattr__",
    "__setitem__",
    "__str__",
    "__sub__",
    "abs",
    "add",
    "addEdge",
    "addNode",
    "all",
    "any",
    "append",
    "apply",
    "assert",
    "bool",
    "buffer",
    "bytes",
    "callable",
    "catch",
    "child_process",
    "classmethod",
    "clear",
    "close",
    "cls",
    "configure",
    "connect",
    "console",
    "constructor",
    "contains",
    "copy",
    "create",
    "crypto",
    "debug",
    "delattr",
    "destroy",
    "dict",
    "disconnect",
    "dispatch",
    "dns",
    "emit",
    "entries",
    "enumerate",
    "error",
    "events",
    "execute",
    "extend",
    "finally",
    "find",
    "findEdge",
    "findIndex",
    "findNode",
    "float",
    "flush",
    "forEach",
    "format",
    "from",
    "fs",
    "get",
    "getEdge",
    "getNode",
    "getStats",
    "getattr",
    "handle",
    "hasEdge",
    "hasNode",
    "hasattr",
    "hash",
    "http",
    "https",
    "id",
    "includes",
    "indexOf",
    "info",
    "initialize",
    "input",
    "int",
    "isNaN",
    "isinstance",
    "issubclass",
    "items",
    "iter",
    "join",
    "keys",
    "len",
    "list",
    "listen",
    "log",
    "match",
    "max",
    "min",
    "net",
    "next",
    "notify",
    "now",
    "object",
    "off",
    "on",
    "once",
    "open",
    "os",
    "parseFloat",
    "parseInt",
    "path",
    "pop",
    "print",
    "process",
    "property",
    "push",
    "queryEdge",
    "queryNode",
    "range",
    "read",
    "receive",
    "reduce",
    "reject",
    "remove",
    "removeEdge",
    "removeNode",
    "render",
    "replace",
    "repr",
    "reset",
    "resolve",
    "reversed",
    "round",
    "run",
    "search",
    "seek",
    "self",
    "send",
    "set",
    "setEdge",
    "setInterval",
    "setNode",
    "setTimeout",
    "setup",
    "slice",
    "sorted",
    "splice",
    "split",
    "start",
    "staticmethod",
    "stop",
    "str",
    "stream",
    "sum",
    "super",
    "teardown",
    "test",
    "then",
    "this",
    "tls",
    "toJSON",
    "toObject",
    "toString",
    "trim",
    "tuple",
    "type",
    "update",
    "updateEdge",
    "updateNode",
    "url",
    "util",
    "validate",
    "valueOf",
    "values",
    "warn",
    "write",
    "zip",
    "zlib",
];

pub fn build(symbols: &[Symbol]) -> Vec<Edge> {
    let mut edges = resolve(symbols);
    edges.extend(test_edges(symbols));
    edges
}

fn resolve(symbols: &[Symbol]) -> Vec<Edge> {
    let mut definitions: HashMap<&str, Vec<(&str, &str, Option<&str>)>> = HashMap::new();
    for sym in symbols {
        if sym.role == "definition" {
            definitions.entry(sym.name.as_str()).or_default().push((
                sym.chunk_id.as_str(),
                sym.path.as_str(),
                sym.container.as_deref(),
            ));
        }
    }
    let mut hints: HashMap<&str, HashSet<&str>> = HashMap::new();
    for sym in symbols {
        if sym.role == "reference" && sym.kind == "module" {
            hints
                .entry(sym.chunk_id.as_str())
                .or_default()
                .insert(sym.name.as_str());
        }
    }
    let mut doc_chunks: Vec<(&str, &str)> = Vec::new();
    for sym in symbols {
        if sym.role == "definition" && !doc_chunks.iter().any(|(path, _)| *path == sym.path) {
            doc_chunks.push((sym.path.as_str(), sym.chunk_id.as_str()));
        }
    }
    let mut edges = Vec::new();
    let mut seen = HashSet::new();
    for sym in symbols {
        if sym.role != "reference" || sym.name.len() < 2 || noise(&sym.name) {
            continue;
        }
        let mut defs = definitions
            .get(sym.name.as_str())
            .cloned()
            .unwrap_or_default();
        if defs.is_empty() && sym.kind == "module" {
            for (path, chunk) in &doc_chunks {
                if module_matches(&sym.name, path) {
                    defs = vec![(chunk, path, None)];
                    break;
                }
            }
        }
        if defs.is_empty() {
            continue;
        }
        // Python's rebuild drops `container` before resolution, so the
        // container filter in resolve_symbols never runs.
        if defs.len() > 1 {
            if let Some(hint) = hints.get(sym.chunk_id.as_str()) {
                let hinted: Vec<_> = defs
                    .iter()
                    .copied()
                    .filter(|(_, path, _)| hint.iter().any(|module| module_matches(module, path)))
                    .collect();
                if !hinted.is_empty() {
                    defs = hinted;
                }
            }
        }
        for (chunk, path, _) in defs {
            if chunk == sym.chunk_id {
                continue;
            }
            let key = (sym.chunk_id.clone(), chunk.to_string(), sym.name.clone());
            if !seen.insert(key) {
                continue;
            }
            let kind = if sym.path == path {
                "intra_file"
            } else {
                "cross_file"
            };
            edges.push(Edge {
                source: sym.chunk_id.clone(),
                target: chunk.to_string(),
                kind: kind.to_string(),
                name: sym.name.clone(),
            });
        }
    }
    edges
}

fn test_edges(symbols: &[Symbol]) -> Vec<Edge> {
    let mut doc_chunk: HashMap<&str, &str> = HashMap::new();
    for sym in symbols {
        doc_chunk
            .entry(sym.path.as_str())
            .or_insert(sym.chunk_id.as_str());
    }
    let mut source_by_stem: HashMap<String, Vec<&str>> = HashMap::new();
    let mut source_docs = Vec::new();
    let mut test_docs = Vec::new();
    for path in doc_chunk.keys().copied() {
        if is_test_path(path) {
            test_docs.push(path);
        } else {
            source_docs.push(path);
            source_by_stem.entry(stem_of(path)).or_default().push(path);
        }
    }
    let mut edges = Vec::new();
    let mut seen = HashSet::new();
    for test_path in test_docs {
        let mut candidates = HashSet::new();
        let name = file_name(test_path);
        if let Some(base) = name.strip_prefix("test_") {
            let base = base.rsplit_once('.').map(|(stem, _)| stem).unwrap_or(base);
            if let Some(paths) = source_by_stem.get(base) {
                candidates.extend(paths.iter().copied());
            }
        }
        let stem = stem_of(test_path);
        if let Some(base) = stem.strip_suffix("_test") {
            if let Some(paths) = source_by_stem.get(base) {
                candidates.extend(paths.iter().copied());
            }
        }
        if is_test_path(test_path) {
            if let Some(paths) = source_by_stem.get(&stem) {
                candidates.extend(paths.iter().copied());
            }
        }
        for sym in symbols {
            if sym.path == test_path
                && sym.role == "reference"
                && (sym.kind == "module" || sym.kind == "import")
            {
                for src in &source_docs {
                    if module_matches(&sym.name, src) {
                        candidates.insert(*src);
                    }
                }
            }
        }
        let test_chunk = doc_chunk[test_path];
        for src in candidates {
            let src_chunk = doc_chunk[src];
            if seen.insert((test_chunk.to_string(), src_chunk.to_string())) {
                edges.push(Edge {
                    source: test_chunk.to_string(),
                    target: src_chunk.to_string(),
                    kind: "test_of".to_string(),
                    name: "test".to_string(),
                });
            }
        }
    }
    edges
}

fn noise(name: &str) -> bool {
    NOISE.binary_search(&name).is_ok()
}

fn is_test_path(path: &str) -> bool {
    let name = file_name(path);
    name.starts_with("test_")
        || stem_of(path).ends_with("_test")
        || path
            .split(['/', '\\'])
            .any(|part| part == "test" || part == "tests")
}

fn file_name(path: &str) -> &str {
    path.rsplit(['/', '\\']).next().unwrap_or(path)
}

fn stem_of(path: &str) -> String {
    file_name(path)
        .rsplit_once('.')
        .map(|(stem, _)| stem)
        .unwrap_or(path)
        .to_string()
}

fn module_matches(module: &str, path: &str) -> bool {
    let norm = path.replace('\\', "/");
    if module.starts_with("./") || module.starts_with("../") {
        let mut clean = module;
        while let Some(rest) = clean
            .strip_prefix("../")
            .or_else(|| clean.strip_prefix("./"))
        {
            clean = rest;
        }
        if clean.is_empty() {
            return false;
        }
        for ext in ["", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"] {
            let suffix = format!("{clean}{ext}");
            if norm.ends_with(&format!("/{suffix}")) || norm == suffix {
                return true;
            }
        }
        for index in [format!("{clean}/index.js"), format!("{clean}/index.ts")] {
            if norm.ends_with(&format!("/{index}")) || norm == index {
                return true;
            }
        }
        return false;
    }
    let parts = module.replace('.', "/");
    if norm.ends_with(&format!("/{parts}.py")) || norm.ends_with(&format!("/{parts}/__init__.py")) {
        return true;
    }
    let clean = module.trim_start_matches('/');
    const WEB: &[&str] = &[
        ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".css", ".scss", ".less",
    ];
    if !clean.is_empty() && WEB.iter().any(|ext| clean.ends_with(ext)) {
        return norm.ends_with(&format!("/{clean}")) || norm == clean;
    }
    false
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sym(name: &str, role: &str, chunk: &str, path: &str) -> Symbol {
        Symbol {
            name: name.to_string(),
            kind: "function".to_string(),
            role: role.to_string(),
            line: 1,
            container: None,
            chunk_id: chunk.to_string(),
            path: path.to_string(),
            chunk_index: 0,
        }
    }

    #[test]
    fn reference_links_to_the_definition_chunk() {
        let symbols = vec![
            sym("Widget", "definition", "def", "rich/widget.py"),
            sym("Widget", "reference", "use", "rich/app.py"),
        ];
        let edges = build(&symbols);
        assert_eq!(edges.len(), 1);
        assert_eq!(edges[0].source, "use");
        assert_eq!(edges[0].target, "def");
        assert_eq!(edges[0].kind, "cross_file");
    }
}
