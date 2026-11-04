//! Line-oriented extractors for Java, Go, Rust, C/C++, Ruby, and PHP.

use super::util::{self, compile, group, match_at};
use super::Hit;

pub(super) fn extract_java(content: &str) -> Vec<Hit> {
    let class_re = compile(
        r"(?:public\s+|private\s+|protected\s+)?(?:abstract\s+)?(?:static\s+)?(?:final\s+)?(?:class|interface|enum)\s+(\w+)",
    );
    let func_re = compile(
        r"(?:public\s+|private\s+|protected\s+)?(?:abstract\s+)?(?:static\s+)?(?:final\s+)?(?:synchronized\s+)?[\w<>\[\]]+\s+(\w+)\s*\(",
    );
    let import_re = compile(r"import\s+(?:static\s+)?([\w.]+)(?:\.\*)?;");
    let mut symbols = Vec::new();
    for (index, line) in util::python_lines(content).into_iter().enumerate() {
        let line_no = index as u32 + 1;
        let stripped = line.trim();
        if let Some(found) = match_at(&class_re, stripped) {
            util::emit(
                &mut symbols,
                group(stripped, &found, 1),
                "class",
                "definition",
                line_no,
                None,
            );
        }
        if let Some(found) = match_at(&func_re, stripped) {
            let name = group(stripped, &found, 1);
            if !matches!(
                name.as_str(),
                "if" | "while" | "for" | "switch" | "catch" | "return" | "new" | "throw"
            ) {
                util::emit(&mut symbols, name, "function", "definition", line_no, None);
            }
        }
        if let Some(found) = match_at(&import_re, stripped) {
            let module = group(stripped, &found, 1);
            util::emit(
                &mut symbols,
                module.clone(),
                "module",
                "reference",
                line_no,
                None,
            );
            if let Some((_, tail)) = module.rsplit_once('.') {
                util::emit(&mut symbols, tail, "import", "reference", line_no, None);
            }
        }
    }
    symbols
}

pub(super) fn extract_go(content: &str) -> Vec<Hit> {
    let func_re = compile(r"func\s+(?:\([^)]+\)\s+)?(\w+)");
    let type_re = compile(r"type\s+(\w+)\s+(?:struct|interface)");
    let import_re = compile(r#"^"([\w./]+)""#);
    let mut symbols = Vec::new();
    for (index, line) in util::python_lines(content).into_iter().enumerate() {
        let line_no = index as u32 + 1;
        let stripped = line.trim();
        if let Some(found) = match_at(&func_re, stripped) {
            util::emit(
                &mut symbols,
                group(stripped, &found, 1),
                "function",
                "definition",
                line_no,
                None,
            );
        }
        if let Some(found) = match_at(&type_re, stripped) {
            util::emit(
                &mut symbols,
                group(stripped, &found, 1),
                "class",
                "definition",
                line_no,
                None,
            );
        }
        if let Some(found) = match_at(&import_re, stripped) {
            util::emit(
                &mut symbols,
                group(stripped, &found, 1),
                "module",
                "reference",
                line_no,
                None,
            );
        }
    }
    symbols
}

pub(super) fn extract_rust(content: &str) -> Vec<Hit> {
    let func_re = compile(r"(?:pub\s+)?(?:async\s+)?fn\s+(\w+)");
    let type_re = compile(r"(?:pub\s+)?(?:struct|enum|trait)\s+(\w+)");
    let impl_re = compile(r"(?:pub\s+)?impl(?:<[^>]+>)?\s+(\w+)");
    let use_re = compile(r"use\s+([\w:]+)");
    let mut symbols = Vec::new();
    for (index, line) in util::python_lines(content).into_iter().enumerate() {
        let line_no = index as u32 + 1;
        let stripped = line.trim();
        if let Some(found) = match_at(&func_re, stripped) {
            util::emit(
                &mut symbols,
                group(stripped, &found, 1),
                "function",
                "definition",
                line_no,
                None,
            );
        }
        if let Some(found) = match_at(&type_re, stripped) {
            util::emit(
                &mut symbols,
                group(stripped, &found, 1),
                "class",
                "definition",
                line_no,
                None,
            );
        }
        if let Some(found) = match_at(&impl_re, stripped) {
            util::emit(
                &mut symbols,
                group(stripped, &found, 1),
                "class",
                "reference",
                line_no,
                None,
            );
        }
        if let Some(found) = match_at(&use_re, stripped) {
            let path = group(stripped, &found, 1);
            util::emit(
                &mut symbols,
                path.clone(),
                "module",
                "reference",
                line_no,
                None,
            );
            let parts: Vec<&str> = path.split("::").collect();
            if parts.len() > 1 {
                util::emit(
                    &mut symbols,
                    parts[parts.len() - 1],
                    "import",
                    "reference",
                    line_no,
                    None,
                );
            }
        }
    }
    symbols
}

pub(super) fn extract_c(content: &str) -> Vec<Hit> {
    let include_re = compile(r#"#include\s+[<"]([^>"]+)[>"]"#);
    let type_re = compile(r"(?:class|struct|enum)\s+(\w+)");
    let func_re = compile(r"(?:[\w:*&]+\s+)+(\w+)\s*\([^)]*\)\s*\{");
    let using_re = compile(r"using\s+([\w.]+)\s*;");
    let mut symbols = Vec::new();
    for (index, line) in util::python_lines(content).into_iter().enumerate() {
        let line_no = index as u32 + 1;
        let stripped = line.trim();
        if let Some(found) = match_at(&include_re, stripped) {
            util::emit(
                &mut symbols,
                group(stripped, &found, 1),
                "module",
                "reference",
                line_no,
                None,
            );
        }
        if let Some(found) = match_at(&type_re, stripped) {
            util::emit(
                &mut symbols,
                group(stripped, &found, 1),
                "class",
                "definition",
                line_no,
                None,
            );
        }
        if let Some(found) = match_at(&func_re, stripped) {
            let name = group(stripped, &found, 1);
            if !matches!(
                name.as_str(),
                "if" | "while" | "for" | "switch" | "return" | "sizeof" | "typeof"
            ) {
                util::emit(&mut symbols, name, "function", "definition", line_no, None);
            }
        }
        if let Some(found) = match_at(&using_re, stripped) {
            util::emit(
                &mut symbols,
                group(stripped, &found, 1),
                "module",
                "reference",
                line_no,
                None,
            );
        }
    }
    symbols
}

pub(super) fn extract_ruby(content: &str) -> Vec<Hit> {
    let def_re = compile(r"def\s+(\w+)");
    let class_re = compile(r"class\s+(\w+)");
    let module_re = compile(r"module\s+(\w+)");
    let require_re = compile(r#"require\s+['"]([^'"]+)['"]"#);
    let mut symbols = Vec::new();
    for (index, line) in util::python_lines(content).into_iter().enumerate() {
        let line_no = index as u32 + 1;
        let stripped = line.trim();
        if let Some(found) = match_at(&def_re, stripped) {
            util::emit(
                &mut symbols,
                group(stripped, &found, 1),
                "function",
                "definition",
                line_no,
                None,
            );
        }
        if let Some(found) = match_at(&class_re, stripped) {
            util::emit(
                &mut symbols,
                group(stripped, &found, 1),
                "class",
                "definition",
                line_no,
                None,
            );
        }
        if let Some(found) = match_at(&module_re, stripped) {
            util::emit(
                &mut symbols,
                group(stripped, &found, 1),
                "class",
                "definition",
                line_no,
                None,
            );
        }
        if let Some(found) = match_at(&require_re, stripped) {
            util::emit(
                &mut symbols,
                group(stripped, &found, 1),
                "module",
                "reference",
                line_no,
                None,
            );
        }
    }
    symbols
}

pub(super) fn extract_php(content: &str) -> Vec<Hit> {
    let type_re = compile(r"(?:abstract\s+)?(?:class|interface|trait)\s+(\w+)");
    let func_re = compile(r"(?:public\s+|private\s+|protected\s+)?(?:static\s+)?function\s+(\w+)");
    let use_re = compile(r"use\s+([\w\\]+)");
    let mut symbols = Vec::new();
    for (index, line) in util::python_lines(content).into_iter().enumerate() {
        let line_no = index as u32 + 1;
        let stripped = line.trim();
        if let Some(found) = match_at(&type_re, stripped) {
            util::emit(
                &mut symbols,
                group(stripped, &found, 1),
                "class",
                "definition",
                line_no,
                None,
            );
        }
        if let Some(found) = match_at(&func_re, stripped) {
            util::emit(
                &mut symbols,
                group(stripped, &found, 1),
                "function",
                "definition",
                line_no,
                None,
            );
        }
        if let Some(found) = match_at(&use_re, stripped) {
            util::emit(
                &mut symbols,
                group(stripped, &found, 1),
                "module",
                "reference",
                line_no,
                None,
            );
        }
    }
    symbols
}
