//! Project root and storage directory.
//!
//! The nearest `.git` (file or directory, so worktrees count) or
//! `.stele-context.toml` wins. With neither, storage is `~/.stele-context`.

use crate::config::Config;
use std::env;
use std::path::{Path, PathBuf};

/// `Path.resolve()`: symlinks and Windows 8.3 short names expanded, as
/// 1.5.2 resolves its working directory. The verbatim prefix Windows adds
/// (`\\?\`) is dropped so the text stays an ordinary path.
pub fn resolved(path: &Path) -> PathBuf {
    let Ok(real) = std::fs::canonicalize(path) else {
        return path.to_path_buf();
    };
    if cfg!(windows) {
        let text = real.to_string_lossy();
        if let Some(rest) = text.strip_prefix(r"\\?\UNC\") {
            return PathBuf::from(format!(r"\\{rest}"));
        }
        if let Some(rest) = text.strip_prefix(r"\\?\") {
            return PathBuf::from(rest);
        }
    }
    real
}

pub fn home_dir() -> Option<PathBuf> {
    if cfg!(windows) {
        env::var_os("USERPROFILE").map(PathBuf::from)
    } else {
        env::var_os("HOME").map(PathBuf::from)
    }
}

pub fn detect_project_root(start: &Path) -> Option<PathBuf> {
    let start = if start.is_absolute() {
        start.to_path_buf()
    } else {
        env::current_dir().ok()?.join(start)
    };
    for dir in start.ancestors() {
        if dir.join(".git").exists() || dir.join(".stele-context.toml").is_file() {
            return Some(dir.to_path_buf());
        }
    }
    None
}

/// The git common directory of the checkout that holds `start`: the
/// `.git` directory of a main checkout, or the one a linked worktree's
/// `.git` file points to through its `commondir`. None outside git.
pub fn git_common_dir(start: &Path) -> Option<PathBuf> {
    for dir in start.ancestors() {
        let git = dir.join(".git");
        let Ok(meta) = std::fs::metadata(&git) else {
            continue;
        };
        if meta.is_dir() {
            return Some(resolved(&git));
        }
        let text = std::fs::read_to_string(&git).ok()?;
        let rest = text.trim().strip_prefix("gitdir:")?;
        let gitdir = PathBuf::from(rest.trim());
        let gitdir = if gitdir.is_absolute() {
            gitdir
        } else {
            dir.join(gitdir)
        };
        return Some(match std::fs::read_to_string(gitdir.join("commondir")) {
            Ok(common) => resolved(&gitdir.join(common.trim())),
            Err(_) => resolved(&gitdir),
        });
    }
    None
}

/// True when the checkout at `root` is a linked git worktree (its `.git`
/// is a file).
pub fn is_linked_worktree(root: &Path) -> bool {
    std::fs::metadata(root.join(".git")).is_ok_and(|meta| meta.is_file())
}

pub fn resolve_storage_dir(
    flag: Option<&Path>,
    config: &Config,
    project_root: Option<&Path>,
) -> Result<PathBuf, String> {
    if let Some(path) = flag {
        return Ok(path.to_path_buf());
    }
    if let Some(path) = &config.storage_dir {
        return Ok(PathBuf::from(path));
    }
    if let Some(path) = env::var_os("STELE_CONTEXT_STORAGE_DIR") {
        return Ok(PathBuf::from(path));
    }
    if let Some(root) = project_root {
        return Ok(root.join(".stele-context"));
    }
    home_dir()
        .map(|home| home.join(".stele-context"))
        .ok_or_else(|| "HOME is unset and no project root was found".to_string())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    #[test]
    fn marker_and_git_walk_up() {
        let root = std::env::temp_dir().join(format!("stele-root-{}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        let nested = root.join("a").join("b");
        fs::create_dir_all(&nested).unwrap();
        fs::write(root.join(".stele-context.toml"), "").unwrap();
        let found = detect_project_root(&nested).unwrap();
        assert_eq!(found, root);
        fs::create_dir_all(nested.join(".git")).unwrap();
        let nearer = detect_project_root(&nested).unwrap();
        assert_eq!(nearer, nested);
        let _ = fs::remove_dir_all(&root);
    }
}
