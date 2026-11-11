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
    // The first definition chunk of each document, in symbol order.
    let mut doc_chunks: Vec<(&str, &str)> = Vec::new();
    let mut doc_seen: HashSet<&str> = HashSet::new();
    for sym in symbols {
        if sym.role == "definition" && doc_seen.insert(sym.path.as_str()) {
            doc_chunks.push((sym.path.as_str(), sym.chunk_id.as_str()));
        }
    }
    let doc_norms: Vec<String> = doc_chunks
        .iter()
        .map(|(path, _)| path.replace('\\', "/"))
        .collect();
    // A module name's first matching document, worked out once per name.
    let mut module_docs: HashMap<&str, Option<(&str, &str)>> = HashMap::new();
    // Prepared hint matchers and `/` paths, built once each.
    let mut matchers: HashMap<&str, ModuleMatch> = HashMap::new();
    let mut norms: HashMap<&str, String> = HashMap::new();
    let mut edges = Vec::new();
    let mut seen: HashSet<(&str, &str, &str)> = HashSet::new();
    for sym in symbols {
        if sym.role != "reference" || sym.name.len() < 2 || noise(&sym.name) {
            continue;
        }
        let mut defs = definitions
            .get(sym.name.as_str())
            .cloned()
            .unwrap_or_default();
        if defs.is_empty() && sym.kind == "module" {
            let found = *module_docs.entry(sym.name.as_str()).or_insert_with(|| {
                let matcher = ModuleMatch::new(&sym.name);
                doc_norms
                    .iter()
                    .position(|norm| matcher.matches(norm))
                    .map(|at| (doc_chunks[at].1, doc_chunks[at].0))
            });
            if let Some((chunk, path)) = found {
                defs = vec![(chunk, path, None)];
            }
        }
        if defs.is_empty() {
            continue;
        }
        // 2.4: a Rust method or associated call (`.update(..)`,
        // `Type::new(..)`) names no receiver type, so it links only to a
        // name defined once; `update` defined in forty files would give
        // every caller forty false edges.
        if sym.kind == "method_call" && defs.len() > 1 {
            continue;
        }
        // Python's rebuild drops `container` before resolution, so the
        // container filter in resolve_symbols never runs.
        if defs.len() > 1 {
            if let Some(hint) = hints.get(sym.chunk_id.as_str()) {
                let hinted: Vec<_> = defs
                    .iter()
                    .copied()
                    .filter(|(_, path, _)| {
                        let norm = norms
                            .entry(path)
                            .or_insert_with(|| path.replace('\\', "/"))
                            .clone();
                        hint.iter().any(|module| {
                            matchers
                                .entry(module)
                                .or_insert_with(|| ModuleMatch::new(module))
                                .matches(&norm)
                        })
                    })
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
            if !seen.insert((sym.chunk_id.as_str(), chunk, sym.name.as_str())) {
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
    // The module and import references of each document, so each test
    // file looks at its own rows only.
    let mut imports: HashMap<&str, Vec<&str>> = HashMap::new();
    for sym in symbols {
        if sym.role == "reference" && (sym.kind == "module" || sym.kind == "import") {
            imports
                .entry(sym.path.as_str())
                .or_default()
                .push(sym.name.as_str());
        }
    }
    let source_norms: Vec<String> = source_docs
        .iter()
        .map(|path| path.replace('\\', "/"))
        .collect();
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
        for name in imports.get(test_path).map(Vec::as_slice).unwrap_or(&[]) {
            let matcher = ModuleMatch::new(name);
            for (src, norm) in source_docs.iter().zip(&source_norms) {
                if matcher.matches(norm) {
                    candidates.insert(*src);
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

/// Whether a module name (`./widget`, `rich.console`, `app.css`) names a
/// document path, prepared once per name, so a name checked against every
/// document does not rebuild its strings per document (2.4; the same
/// answers as the per-call check it replaces).
struct ModuleMatch {
    /// The path is this, or ends with `/` + this.
    whole: Vec<String>,
    /// The path ends with one of these (Python modules).
    tails: Vec<String>,
}

impl ModuleMatch {
    fn new(module: &str) -> ModuleMatch {
        if module.starts_with("./") || module.starts_with("../") {
            let mut clean = module;
            while let Some(rest) = clean
                .strip_prefix("../")
                .or_else(|| clean.strip_prefix("./"))
            {
                clean = rest;
            }
            if clean.is_empty() {
                return ModuleMatch {
                    whole: Vec::new(),
                    tails: Vec::new(),
                };
            }
            let mut whole: Vec<String> = ["", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"]
                .iter()
                .map(|ext| format!("{clean}{ext}"))
                .collect();
            whole.push(format!("{clean}/index.js"));
            whole.push(format!("{clean}/index.ts"));
            return ModuleMatch {
                whole,
                tails: Vec::new(),
            };
        }
        let parts = module.replace('.', "/");
        let tails = vec![format!("/{parts}.py"), format!("/{parts}/__init__.py")];
        let clean = module.trim_start_matches('/');
        const WEB: &[&str] = &[
            ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".css", ".scss", ".less",
        ];
        let whole = if !clean.is_empty() && WEB.iter().any(|ext| clean.ends_with(ext)) {
            vec![clean.to_string()]
        } else {
            Vec::new()
        };
        ModuleMatch { whole, tails }
    }

    /// `norm` is the document path with `/` separators.
    fn matches(&self, norm: &str) -> bool {
        self.tails.iter().any(|tail| norm.ends_with(tail.as_str()))
            || self.whole.iter().any(|whole| {
                norm == whole
                    || (norm.len() > whole.len()
                        && norm.ends_with(whole.as_str())
                        && norm.as_bytes()[norm.len() - whole.len() - 1] == b'/')
            })
    }
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
    fn module_names_match_document_paths() {
        let norm = |path: &str| path.replace('\\', "/");
        let cases = [
            ("./widget", "src\\widget.js", true),
            ("../lib/widget", "src/lib/widget/index.ts", true),
            ("./widget", "src/mywidget.js", false),
            ("./", "src/a.js", false),
            ("rich.console", "src/rich/console.py", true),
            ("rich.console", "rich/console/__init__.py", false),
            ("rich.console", "x/rich/console/__init__.py", true),
            ("app.css", "web/app.css", true),
            ("app.css", "app.css", true),
            ("app.css", "web/xapp.css", false),
            ("crate::a::B", "src/a.rs", false),
        ];
        for (module, path, want) in cases {
            assert_eq!(
                ModuleMatch::new(module).matches(&norm(path)),
                want,
                "{module} {path}"
            );
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
