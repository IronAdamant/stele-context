//! `doctor.python`: the version of the host Python, read from its files.
//!
//! 1.5.2 reports `sys.version.split()[0]` of the interpreter that runs it.
//! The 2.0 binary has no interpreter and never starts one. It reports the
//! interpreter PATH resolves for `python3`, then `python`, and reads the
//! version that interpreter was built with:
//!
//! 1. a virtual environment's `pyvenv.cfg` points at its base install
//!    (`home`), whose headers are read next; `version_info` / `version` in
//!    the cfg are the fallback;
//! 2. `PY_VERSION` in the install's `patchlevel.h` (the constant
//!    `sys.version` starts with);
//! 3. the same `PY_VERSION` string compiled into the interpreter binary or
//!    its shared library.
//!
//! No candidate: `null`, as when 1.5.2 would have no interpreter to name.

use std::fs;
use std::path::{Path, PathBuf};
use std::sync::OnceLock;

/// The first word of the host `sys.version`, or empty when no interpreter
/// is found. Computed once per process.
pub fn host_version() -> &'static str {
    static FOUND: OnceLock<String> = OnceLock::new();
    FOUND.get_or_init(|| {
        for name in ["python3", "python"] {
            if let Some(exe) = which(name) {
                if let Some(version) = version_of(&exe) {
                    return version;
                }
            }
        }
        String::new()
    })
}

fn which(name: &str) -> Option<PathBuf> {
    let path = std::env::var_os("PATH")?;
    let file = if cfg!(windows) {
        format!("{name}.exe")
    } else {
        name.to_string()
    };
    for dir in std::env::split_paths(&path) {
        let candidate = dir.join(&file);
        // A Windows App Execution Alias (the Store `python3` stub) is a
        // zero-length reparse point, not an interpreter.
        match fs::metadata(&candidate) {
            Ok(meta) if meta.is_file() && meta.len() > 0 => return Some(candidate),
            _ => continue,
        }
    }
    None
}

fn version_of(exe: &Path) -> Option<String> {
    let dir = exe.parent()?;
    for cfg_dir in [Some(dir), dir.parent()].into_iter().flatten() {
        let cfg = cfg_dir.join("pyvenv.cfg");
        if let Ok(text) = fs::read_to_string(&cfg) {
            let keys = cfg_keys(&text);
            let short = keys
                .iter()
                .find(|(key, _)| key == "version_info" || key == "version")
                .map(|(_, value)| value.clone());
            let minor = short.as_deref().and_then(major_minor);
            if let Some((_, home)) = keys.iter().find(|(key, _)| key == "home") {
                let home = PathBuf::from(home);
                if let Some(version) = install_version(&home, None, minor.as_deref()) {
                    return Some(version);
                }
            }
            return short.map(|value| venv_version(&value));
        }
    }
    let real = fs::canonicalize(exe).unwrap_or_else(|_| exe.to_path_buf());
    let real_dir = real.parent()?;
    let tag = real
        .file_name()
        .and_then(|name| name.to_str())
        .and_then(|name| name.strip_suffix(".exe").or(Some(name)))
        .and_then(|name| name.strip_prefix("python"))
        .filter(|rest| rest.contains('.'))
        .map(str::to_string);
    install_version(real_dir, Some(&real), tag.as_deref())
}

/// `tag` is `3.13` or `3.13t` when the binary name carries it.
fn install_version(bin_dir: &Path, exe: Option<&Path>, tag: Option<&str>) -> Option<String> {
    let prefix = bin_dir.parent();
    let mut headers: Vec<PathBuf> = vec![bin_dir.join("include").join("patchlevel.h")];
    let mut tags: Vec<String> = Vec::new();
    if let Some(tag) = tag {
        tags.push(tag.to_string());
    } else if let Some(prefix) = prefix {
        if let Ok(rows) = fs::read_dir(prefix.join("include")) {
            let mut found: Vec<String> = rows
                .flatten()
                .filter_map(|row| row.file_name().to_str().map(str::to_string))
                .filter_map(|name| name.strip_prefix("python").map(str::to_string))
                .filter(|rest| rest.starts_with("3."))
                .collect();
            found.sort();
            if found.len() == 1 {
                tags.extend(found);
            }
        }
    }
    if let Some(prefix) = prefix {
        for tag in &tags {
            headers.push(
                prefix
                    .join("include")
                    .join(format!("python{tag}"))
                    .join("patchlevel.h"),
            );
        }
    }
    for header in &headers {
        if let Some(version) = fs::read_to_string(header)
            .ok()
            .and_then(|text| patchlevel(&text))
        {
            return Some(version);
        }
    }
    let mut minor: Option<String> = tags.first().map(|tag| {
        tag.trim_end_matches(|ch: char| ch.is_ascii_alphabetic())
            .to_string()
    });
    if minor.is_none() {
        // Windows installs and embeddable zips name the runtime python3XY.dll.
        minor = fs::read_dir(bin_dir).ok().and_then(|rows| {
            rows.flatten().find_map(|row| {
                let name = row.file_name().to_str()?.to_ascii_lowercase();
                let digits = name.strip_prefix("python3")?.strip_suffix(".dll")?;
                if !digits.is_empty() && digits.bytes().all(|byte| byte.is_ascii_digit()) {
                    Some(format!("3.{digits}"))
                } else {
                    None
                }
            })
        });
    }
    let minor = minor?;
    let mut binaries: Vec<PathBuf> = Vec::new();
    if let Some(exe) = exe {
        binaries.push(exe.to_path_buf());
    }
    for tag in &tags {
        binaries.push(bin_dir.join(format!("python{tag}")));
    }
    let digits = minor.replace('.', "");
    binaries.push(bin_dir.join(format!("python{digits}.dll")));
    if let Some(prefix) = prefix {
        let lib = prefix.join("lib");
        for name in [
            format!("libpython{minor}.so.1.0"),
            format!("libpython{minor}.so"),
            format!("libpython{minor}.dylib"),
        ] {
            binaries.push(lib.join(name));
        }
        binaries.push(prefix.join("Python"));
    }
    binaries.iter().find_map(|path| {
        fs::read(path)
            .ok()
            .and_then(|bytes| compiled(&bytes, &minor))
    })
}

fn cfg_keys(text: &str) -> Vec<(String, String)> {
    text.lines()
        .filter_map(|line| {
            let (key, value) = line.split_once('=')?;
            Some((key.trim().to_ascii_lowercase(), value.trim().to_string()))
        })
        .collect()
}

fn major_minor(version: &str) -> Option<String> {
    let mut parts = version.split('.');
    let major = parts.next()?;
    let minor = parts.next()?;
    Some(format!("{major}.{minor}"))
}

/// `pyvenv.cfg` spells a release `3.13.5`, `3.13.5.final.0` (virtualenv),
/// or `3.14.0.candidate.1`. `sys.version` spells them `3.13.5` and
/// `3.14.0rc1`.
fn venv_version(value: &str) -> String {
    let parts: Vec<&str> = value.split('.').collect();
    if parts.len() >= 5 {
        let base = parts[..3].join(".");
        let suffix = match parts[3] {
            "alpha" => "a",
            "beta" => "b",
            "candidate" => "rc",
            _ => return base,
        };
        return format!("{base}{suffix}{}", parts[4]);
    }
    value.to_string()
}

/// `#define PY_VERSION "3.13.5"` from `patchlevel.h`.
fn patchlevel(text: &str) -> Option<String> {
    for line in text.lines() {
        let rest = line.trim_start().strip_prefix("#define")?;
        let rest = rest.trim_start();
        let Some(rest) = rest.strip_prefix("PY_VERSION") else {
            continue;
        };
        if !rest.starts_with(char::is_whitespace) {
            continue;
        }
        let rest = rest.trim();
        let inner = rest.strip_prefix('"')?.split('"').next()?;
        if !inner.is_empty() {
            return Some(inner.to_string());
        }
    }
    None
}

/// The NUL-terminated `PY_VERSION` literal (`3.12.3`, `3.14.0rc1`) inside a
/// CPython binary built for `minor`.
fn compiled(bytes: &[u8], minor: &str) -> Option<String> {
    let needle = format!("{minor}.");
    let needle = needle.as_bytes();
    let mut start = 0usize;
    while start + needle.len() < bytes.len() {
        let hit = bytes[start..]
            .windows(needle.len())
            .position(|window| window == needle)?;
        let at = start + hit;
        start = at + 1;
        if at == 0 || bytes[at - 1] != 0 {
            continue;
        }
        let mut end = at + needle.len();
        let digits_from = end;
        while end < bytes.len() && bytes[end].is_ascii_digit() {
            end += 1;
        }
        if end == digits_from {
            continue;
        }
        while end < bytes.len() && (bytes[end].is_ascii_alphanumeric() || bytes[end] == b'+') {
            end += 1;
        }
        if end < bytes.len() && bytes[end] == 0 && end - at <= 16 {
            return std::str::from_utf8(&bytes[at..end])
                .ok()
                .map(str::to_string);
        }
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reads_patchlevel_and_venv_spellings() {
        let header = "#define PY_MINOR_VERSION 13\n#define PY_VERSION      \t\"3.13.5\"\n";
        assert_eq!(patchlevel(header).as_deref(), Some("3.13.5"));
        assert_eq!(venv_version("3.13.5"), "3.13.5");
        assert_eq!(venv_version("3.13.5.final.0"), "3.13.5");
        assert_eq!(venv_version("3.14.0.candidate.1"), "3.14.0rc1");
        let mut blob = b"junk\x003.12.0abc\x00\x003.12.3\x00tail".to_vec();
        blob.extend_from_slice(b"\x003.12.\x00");
        assert_eq!(compiled(&blob, "3.12").as_deref(), Some("3.12.0abc"));
        assert_eq!(
            compiled(b"\x003.13.15\x00", "3.13").as_deref(),
            Some("3.13.15")
        );
    }

    /// The value 1.5.2 would report for the reference interpreter: run it
    /// (test only) and compare with the file-based lookup of that same
    /// interpreter.
    #[test]
    fn matches_the_reference_interpreter() {
        let bin = crate::oracle::plain();
        let out = std::process::Command::new(bin)
            .args([
                "-c",
                "import sys; print(sys.executable); print(sys.version.split()[0])",
            ])
            .output()
            .expect("python");
        let text = String::from_utf8_lossy(&out.stdout);
        let mut lines = text.lines();
        let exe = PathBuf::from(lines.next().unwrap_or("").trim());
        let expect = lines.next().unwrap_or("").trim().to_string();
        assert_eq!(version_of(&exe).as_deref(), Some(expect.as_str()));
    }
}
