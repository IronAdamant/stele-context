//! Directory walk used by indexing. Dot directories and the default skip
//! set are omitted. An explicit file is kept even when gitignore matches it.

use crate::gitignore::Gitignore;
use std::fs;
use std::io;
use std::path::{Path, PathBuf};

pub const SKIP_DIRS: &[&str] = &[
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    ".tox",
    ".eggs",
    "dist",
    "build",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
];

pub const CODE_EXTENSIONS: &[&str] = &[
    "py", "pyw", "pyx", "js", "jsx", "mjs", "cjs", "ts", "tsx", "java", "kt", "kts", "scala", "c",
    "h", "cpp", "cc", "cxx", "hpp", "hxx", "cs", "go", "rs", "rb", "php", "swift", "sh", "bash",
    "zsh", "json", "yaml", "yml", "toml", "ini", "cfg", "sql", "html", "htm", "css", "scss",
    "less",
];

pub const TEXT_EXTENSIONS: &[&str] = &["txt", "md", "markdown", "rst", "adoc", "log", "csv", "tsv"];

pub const MEDIA_EXTENSIONS: &[&str] = &[
    "png", "jpg", "jpeg", "gif", "webp", "bmp", "tiff", "tif", "ico", "mp3", "wav", "ogg", "flac",
    "m4a", "aac", "wma", "mp4", "mov", "mkv", "webm", "avi", "pdf",
];

pub fn is_supported_extension(ext: &str) -> bool {
    CODE_EXTENSIONS.contains(&ext)
        || TEXT_EXTENSIONS.contains(&ext)
        || MEDIA_EXTENSIONS.contains(&ext)
}

pub fn expand(paths: &[PathBuf], respect_gitignore: bool) -> io::Result<Vec<String>> {
    let mut out = Vec::new();
    for path in paths {
        if path.is_file() {
            out.push(normalize(path));
            continue;
        }
        if path.is_dir() {
            let matcher = if respect_gitignore {
                Gitignore::load(path)
            } else {
                None
            };
            walk_dir(path, path, matcher.as_ref(), &mut out)?;
        }
    }
    out.sort();
    out.dedup();
    Ok(out)
}

fn walk_dir(
    root: &Path,
    dir: &Path,
    matcher: Option<&Gitignore>,
    out: &mut Vec<String>,
) -> io::Result<()> {
    let mut entries: Vec<_> = fs::read_dir(dir)?.collect::<Result<Vec<_>, _>>()?;
    entries.sort_by_key(|entry| entry.file_name());
    for entry in entries {
        let name = entry.file_name();
        let name = name.to_string_lossy();
        if name.starts_with('.') || SKIP_DIRS.contains(&name.as_ref()) {
            continue;
        }
        let path = entry.path();
        let rel = normalize_under(root, &path);
        let file_type = entry.file_type()?;
        if file_type.is_dir() {
            if matcher.is_some_and(|rules| rules.is_ignored(&rel, true)) {
                continue;
            }
            walk_dir(root, &path, matcher, out)?;
        } else if file_type.is_file() {
            let ext = path
                .extension()
                .and_then(|ext| ext.to_str())
                .unwrap_or("")
                .to_ascii_lowercase();
            if !is_supported_extension(&ext) {
                continue;
            }
            if matcher.is_some_and(|rules| rules.is_ignored(&rel, false)) {
                continue;
            }
            out.push(rel);
        }
    }
    Ok(())
}

fn normalize(path: &Path) -> String {
    path.to_string_lossy().replace('\\', "/")
}

fn normalize_under(root: &Path, path: &Path) -> String {
    path.strip_prefix(root)
        .unwrap_or(path)
        .to_string_lossy()
        .replace('\\', "/")
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    #[test]
    fn skips_ignored_and_dot_directories() {
        let root = std::env::temp_dir().join(format!("stele-walk-{}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(root.join("secrets")).unwrap();
        fs::create_dir_all(root.join(".hidden")).unwrap();
        fs::write(root.join(".gitignore"), "ignored.py\nsecrets/\n").unwrap();
        fs::write(root.join("kept.py"), "def kept():\n    return 1\n").unwrap();
        fs::write(root.join("ignored.py"), "x\n").unwrap();
        fs::write(root.join("secrets").join("keys.txt"), "x\n").unwrap();
        fs::write(root.join(".hidden").join("no.py"), "x\n").unwrap();
        let found = expand(&[root.clone()], true).unwrap();
        assert!(found.iter().any(|path| path.ends_with("kept.py")));
        assert!(!found.iter().any(|path| path.contains("ignored.py")));
        assert!(!found.iter().any(|path| path.contains("secrets")));
        assert!(!found.iter().any(|path| path.contains("hidden")));
        let explicit = expand(&[root.join("ignored.py")], true).unwrap();
        assert_eq!(explicit.len(), 1);
        let _ = fs::remove_dir_all(&root);
    }
}
