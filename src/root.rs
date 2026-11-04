//! Project root and storage directory.
//!
//! The nearest `.git` (file or directory, so worktrees count) or
//! `.stele-context.toml` wins. With neither, storage is `~/.stele-context`.

use crate::config::Config;
use std::env;
use std::path::{Path, PathBuf};

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
