//! HTML and CSS extraction. These extractors do not record line numbers.

use super::util::{self, compile, find_all, group};
use super::Hit;

pub(super) fn extract_html(content: &str) -> Vec<Hit> {
    let class_re = compile(r#"class\s*=\s*["\']([^"\']+)["\']"#);
    let id_re = compile(r#"id\s*=\s*["\']([^"\']+)["\']"#);
    let script_re = compile(r#"<script[^>]+src\s*=\s*["\']([^"\']+)["\']"#);
    let link_re = compile(r#"<link[^>]+href\s*=\s*["\']([^"\']+)["\']"#);
    let handler_re = compile(r#"on\w+\s*=\s*["\'](\w+)\s*\("#);
    let mut symbols = Vec::new();
    for found in find_all(&class_re, content) {
        for class_name in group(content, &found, 1).split_whitespace() {
            util::emit(
                &mut symbols,
                format!(".{class_name}"),
                "css_class",
                "reference",
                0,
                None,
            );
        }
    }
    for found in find_all(&id_re, content) {
        util::emit(
            &mut symbols,
            format!("#{}", group(content, &found, 1)),
            "css_id",
            "reference",
            0,
            None,
        );
    }
    for found in find_all(&script_re, content) {
        util::emit(
            &mut symbols,
            group(content, &found, 1),
            "module",
            "reference",
            0,
            None,
        );
    }
    for found in find_all(&link_re, content) {
        let href = group(content, &found, 1);
        if href.ends_with(".css") || href.ends_with(".scss") || href.ends_with(".less") {
            util::emit(&mut symbols, href, "module", "reference", 0, None);
        }
    }
    for found in find_all(&handler_re, content) {
        util::emit(
            &mut symbols,
            group(content, &found, 1),
            "function",
            "reference",
            0,
            None,
        );
    }
    symbols
}

pub(super) fn extract_css(content: &str) -> Vec<Hit> {
    let clean = strip_block_comments(content);
    let class_re = compile(r"\.([a-zA-Z_][\w-]*)\s*[{,:\s]");
    let id_re = compile(r"#([a-zA-Z_][\w-]*)\s*[{,:\s]");
    let import_re = compile(r#"@import\s+["\']([^"\']+)["\']"#);
    let url_re = compile(r#"url\(["\']?([^)"\'\s]+)["\']?\)"#);
    let mut symbols = Vec::new();
    for found in find_all(&class_re, &clean) {
        util::emit(
            &mut symbols,
            format!(".{}", group(&clean, &found, 1)),
            "css_class",
            "definition",
            0,
            None,
        );
    }
    for found in find_all(&id_re, &clean) {
        util::emit(
            &mut symbols,
            format!("#{}", group(&clean, &found, 1)),
            "css_id",
            "definition",
            0,
            None,
        );
    }
    for found in find_all(&import_re, &clean) {
        util::emit(
            &mut symbols,
            group(&clean, &found, 1),
            "module",
            "reference",
            0,
            None,
        );
    }
    for found in find_all(&url_re, &clean) {
        util::emit(
            &mut symbols,
            group(&clean, &found, 1),
            "module",
            "reference",
            0,
            None,
        );
    }
    symbols
}

fn strip_block_comments(content: &str) -> String {
    let chars: Vec<char> = content.chars().collect();
    let mut out = String::new();
    let mut index = 0;
    while index < chars.len() {
        if chars[index] == '/' && index + 1 < chars.len() && chars[index + 1] == '*' {
            if let Some(end) = chars[index + 2..]
                .windows(2)
                .position(|window| window == ['*', '/'])
            {
                index = index + 2 + end + 2;
                continue;
            }
        }
        out.push(chars[index]);
        index += 1;
    }
    out
}
