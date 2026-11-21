//! Directory walk used by indexing. Dot directories and the default skip
//! set are omitted. An explicit file is kept even when gitignore matches it.
//!
//! - Gitignore: the walked directory's `.gitignore`, every `.gitignore`
//!   below it (for its own subtree), and, inside a git checkout, those of
//!   the directories above it up to the checkout root. A deeper file's
//!   match wins, as in git.
//! - Tool state: a directory that holds Wikifier state (`file_health.json`,
//!   `map_paths.txt` or `.wikifier_staging/wk_cache`) has its generated
//!   files skipped (`WIKIFIER_STATE`, and `index.html` when it is
//!   Wikifier's dashboard). Naming one explicitly still indexes it.

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

/// Files and directories Wikifier writes into a project root.
pub const WIKIFIER_STATE: &[&str] = &[
    "file_health.json",
    "file_health.md",
    "library.md",
    "pending_updates.md",
    "map_paths.txt",
    "monitored_paths.txt",
    "exclude_patterns.txt",
    "journal",
    "Logged_issues",
];

/// `dir` holds Wikifier's state (its own `has_state` test).
pub fn is_wikifier_root(dir: &Path) -> bool {
    dir.join("file_health.json").is_file()
        || dir.join("map_paths.txt").is_file()
        || dir.join(".wikifier_staging").join("wk_cache").is_dir()
}

/// `path` is the dashboard `wikifier init` copies in (about 11k tokens of
/// HTML and script): its title names Wikifier.
fn is_wikifier_dashboard(path: &Path) -> bool {
    use std::io::Read;
    let mut head = [0u8; 2048];
    let Ok(mut file) = fs::File::open(path) else {
        return false;
    };
    let read = file.read(&mut head).unwrap_or(0);
    let text = String::from_utf8_lossy(&head[..read]);
    text.find("<title>")
        .and_then(|at| {
            let rest = &text[at..];
            rest.find("</title>")
                .map(|end| rest[..end].contains("Wikifier"))
        })
        .unwrap_or(false)
}

/// One `.gitignore` and how to turn a walk-relative path into one
/// relative to its directory: put `lead` in front (a file above the walk
/// root) or take `strip` off (a file below it).
struct Ignore {
    rules: Gitignore,
    lead: String,
    strip: String,
}

impl Ignore {
    fn verdict(&self, rel: &str, is_dir: bool) -> Option<bool> {
        let rel = rel.replace('\\', "/");
        let local = rel.strip_prefix(self.strip.as_str())?;
        self.rules.verdict(&format!("{}{local}", self.lead), is_dir)
    }
}

fn ignored(stack: &[Ignore], rel: &str, is_dir: bool) -> bool {
    let mut verdict = None;
    for one in stack {
        if let Some(found) = one.verdict(rel, is_dir) {
            verdict = Some(found);
        }
    }
    verdict == Some(true)
}

/// The `.gitignore` files above `dir` up to its git checkout root,
/// outermost first. None outside a checkout.
fn ancestor_ignores(dir: &Path) -> Vec<Ignore> {
    let dir = crate::root::resolved(dir);
    let mut found = Vec::new();
    let mut lead = String::new();
    let mut current = dir.as_path();
    if current.join(".git").exists() {
        return found;
    }
    while let Some(parent) = current.parent() {
        let name = current
            .file_name()
            .map(|name| name.to_string_lossy().to_string())
            .unwrap_or_default();
        lead = format!("{name}/{lead}");
        if let Some(rules) = Gitignore::load(parent) {
            found.push(Ignore {
                rules,
                lead: lead.clone(),
                strip: String::new(),
            });
        }
        if parent.join(".git").exists() {
            found.reverse();
            return found;
        }
        current = parent;
    }
    Vec::new()
}

pub fn expand(
    paths: &[PathBuf],
    respect_gitignore: bool,
    extra_skip: &[String],
) -> io::Result<Vec<String>> {
    let mut out = Vec::new();
    for path in paths {
        if path.is_file() {
            out.push(normalize(path));
            continue;
        }
        if path.is_dir() {
            let stack = if respect_gitignore {
                ancestor_ignores(path)
            } else {
                Vec::new()
            };
            let mut found = Vec::new();
            let mut walk = Walk {
                root: path,
                respect_gitignore,
                extra_skip,
                stack,
                out: &mut found,
            };
            walk.dir(path)?;
            // 1.5.2 `sorted(p.rglob("*"))`: paths compare part by part, and
            // a Windows path compares case-insensitively.
            found.sort_by(|left, right| path_order(left, right));
            out.extend(found);
        }
    }
    let mut seen = std::collections::HashSet::new();
    out.retain(|path| seen.insert(path.clone()));
    Ok(out)
}

/// `PurePath.__lt__`: the parts compared in order, each lowercased on
/// Windows.
pub fn path_order(left: &str, right: &str) -> std::cmp::Ordering {
    let parts = |text: &str| -> Vec<String> {
        text.split(['/', '\\'])
            .map(|part| {
                if cfg!(windows) {
                    part.to_lowercase()
                } else {
                    part.to_string()
                }
            })
            .collect()
    };
    parts(left).cmp(&parts(right))
}

struct Walk<'a> {
    root: &'a Path,
    respect_gitignore: bool,
    extra_skip: &'a [String],
    stack: Vec<Ignore>,
    out: &'a mut Vec<String>,
}

impl Walk<'_> {
    fn dir(&mut self, dir: &Path) -> io::Result<()> {
        let pushed = if self.respect_gitignore {
            match Gitignore::load(dir) {
                Some(rules) => {
                    let mut strip = normalize_under(self.root, dir).replace('\\', "/");
                    if !strip.is_empty() && dir != self.root {
                        strip.push('/');
                    } else {
                        strip.clear();
                    }
                    self.stack.push(Ignore {
                        rules,
                        lead: String::new(),
                        strip,
                    });
                    true
                }
                None => false,
            }
        } else {
            false
        };
        let result = self.entries(dir);
        if pushed {
            self.stack.pop();
        }
        result
    }

    fn entries(&mut self, dir: &Path) -> io::Result<()> {
        let mut entries: Vec<_> = fs::read_dir(dir)?.collect::<Result<Vec<_>, _>>()?;
        entries.sort_by_key(|entry| entry.file_name());
        let wikifier = is_wikifier_root(dir);
        for entry in entries {
            let name = entry.file_name();
            let name = name.to_string_lossy();
            if name.starts_with('.')
                || SKIP_DIRS.contains(&name.as_ref())
                || self.extra_skip.iter().any(|skip| skip == name.as_ref())
            {
                continue;
            }
            let path = entry.path();
            if wikifier
                && (WIKIFIER_STATE.contains(&name.as_ref())
                    || (name == "index.html" && is_wikifier_dashboard(&path)))
            {
                continue;
            }
            let rel = normalize_under(self.root, &path);
            let file_type = entry.file_type()?;
            if file_type.is_dir() {
                if ignored(&self.stack, &rel, true) {
                    continue;
                }
                self.dir(&path)?;
            } else if file_type.is_file() {
                let ext = path
                    .extension()
                    .and_then(|ext| ext.to_str())
                    .unwrap_or("")
                    .to_ascii_lowercase();
                if !is_supported_extension(&ext) {
                    continue;
                }
                if ignored(&self.stack, &rel, false) {
                    continue;
                }
                self.out.push(rel);
            }
        }
        Ok(())
    }
}

fn normalize(path: &Path) -> String {
    native_sep(&path.to_string_lossy())
}

fn normalize_under(root: &Path, path: &Path) -> String {
    native_sep(&path.strip_prefix(root).unwrap_or(path).to_string_lossy())
}

/// Match 1.5.2 `Path` text: forward slashes on Unix, backslashes on Windows.
pub fn native_sep(path: &str) -> String {
    if cfg!(windows) {
        path.replace('/', "\\")
    } else {
        path.replace('\\', "/")
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    #[test]
    fn skips_ignored_and_dot_directories() {
        let root = crate::test_dir("walk");
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(root.join("secrets")).unwrap();
        fs::create_dir_all(root.join(".hidden")).unwrap();
        fs::write(root.join(".gitignore"), "ignored.py\nsecrets/\n").unwrap();
        fs::write(root.join("kept.py"), "def kept():\n    return 1\n").unwrap();
        fs::write(root.join("ignored.py"), "x\n").unwrap();
        fs::write(root.join("secrets").join("keys.txt"), "x\n").unwrap();
        fs::write(root.join(".hidden").join("no.py"), "x\n").unwrap();
        let found = expand(&[root.clone()], true, &[]).unwrap();
        assert!(found.iter().any(|path| path.ends_with("kept.py")));
        assert!(!found.iter().any(|path| path.contains("ignored.py")));
        assert!(!found.iter().any(|path| path.contains("secrets")));
        assert!(!found.iter().any(|path| path.contains("hidden")));
        let explicit = expand(&[root.join("ignored.py")], true, &[]).unwrap();
        assert_eq!(explicit.len(), 1);
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn nested_and_parent_gitignores_apply() {
        let root = crate::test_dir("walk-nested");
        fs::create_dir_all(root.join(".git")).unwrap();
        fs::create_dir_all(root.join("pkg/gen")).unwrap();
        fs::create_dir_all(root.join("pkg/src")).unwrap();
        fs::write(root.join(".gitignore"), "*.log\n/pkg/src/secret.py\n").unwrap();
        fs::write(
            root.join("pkg/.gitignore"),
            "gen/\nlocal_*.py\n!local_keep.py\n",
        )
        .unwrap();
        fs::write(root.join("pkg/src/a.py"), "x = 1\n").unwrap();
        fs::write(root.join("pkg/src/secret.py"), "x = 1\n").unwrap();
        fs::write(root.join("pkg/src/run.log"), "x\n").unwrap();
        fs::write(root.join("pkg/src/local_tmp.py"), "x = 1\n").unwrap();
        fs::write(root.join("pkg/src/local_keep.py"), "x = 1\n").unwrap();
        fs::write(root.join("pkg/gen/out.py"), "x = 1\n").unwrap();
        let names = |found: Vec<String>| -> Vec<String> {
            found
                .into_iter()
                .map(|path| path.replace('\\', "/"))
                .collect()
        };
        // From the checkout root: the nested pkg/.gitignore applies below pkg.
        let found = names(expand(&[root.clone()], true, &[]).unwrap());
        assert_eq!(
            found,
            vec![
                "pkg/src/a.py".to_string(),
                "pkg/src/local_keep.py".to_string()
            ],
        );
        // From a directory inside the checkout: the files above it apply too.
        let found = names(expand(&[root.join("pkg").join("src")], true, &[]).unwrap());
        assert_eq!(found, vec!["a.py".to_string(), "local_keep.py".to_string()]);
        // respect_gitignore=false walks everything supported.
        let found = expand(&[root.join("pkg")], false, &[]).unwrap();
        assert_eq!(found.len(), 6, "{found:?}");
        let _ = fs::remove_dir_all(&root);
    }

    /// A/B run, 2026-11-21: a project-wide index picked up `wikifier init`'s
    /// dashboard (`index.html`, about 11k tokens) and its state files.
    #[test]
    fn wikifier_state_is_skipped_unless_named() {
        let root = crate::test_dir("walk-wikifier");
        fs::create_dir_all(root.join("journal")).unwrap();
        fs::create_dir_all(root.join("web")).unwrap();
        fs::write(root.join("file_health.json"), "{}\n").unwrap();
        fs::write(root.join("file_health.md"), "# health\n").unwrap();
        fs::write(root.join("library.md"), "# library\n").unwrap();
        fs::write(root.join("pending_updates.md"), "# pending\n").unwrap();
        fs::write(root.join("map_paths.txt"), "src\n").unwrap();
        fs::write(root.join("journal").join("2026-11-21.md"), "x\n").unwrap();
        fs::write(
            root.join("index.html"),
            "<!doctype html><html><head><title>Project Wiki \u{2014} Wikifier</title></head></html>\n",
        )
        .unwrap();
        fs::write(
            root.join("web").join("index.html"),
            "<html><head><title>Shop</title></head></html>\n",
        )
        .unwrap();
        fs::write(root.join("main.py"), "x = 1\n").unwrap();
        fs::write(root.join("README.md"), "# readme\n").unwrap();
        let found: Vec<String> = expand(&[root.clone()], true, &[])
            .unwrap()
            .into_iter()
            .map(|path| path.replace('\\', "/"))
            .collect();
        // Windows orders paths case-insensitively, as 1.5.2's `sorted()` did there.
        let mut want = vec!["README.md", "main.py", "web/index.html"];
        want.sort_by(|left, right| path_order(left, right));
        assert_eq!(found, want);
        // A user's own index.html in a Wikifier root stays.
        fs::write(
            root.join("index.html"),
            "<html><title>Shop</title></html>\n",
        )
        .unwrap();
        let found = expand(&[root.clone()], true, &[]).unwrap();
        assert!(found.iter().any(|path| path == "index.html"), "{found:?}");
        // Named explicitly, state is indexed.
        let named = expand(&[root.join("library.md"), root.join("journal")], true, &[]).unwrap();
        assert_eq!(named.len(), 2, "{named:?}");
        // Without Wikifier state, the names are ordinary files.
        fs::remove_file(root.join("file_health.json")).unwrap();
        fs::remove_file(root.join("map_paths.txt")).unwrap();
        let found = expand(&[root.clone()], true, &[]).unwrap();
        assert!(found.iter().any(|path| path == "library.md"), "{found:?}");
        let _ = fs::remove_dir_all(&root);
    }
}
