//! Python helpers shared by `stele-bench versus` and `tests/mcp_parity.rs`.
//!
//! Both compare this crate with the unmodified PyPI stele-context 1.5.2. The
//! test includes this file with `#[path]`, so each item here is used by both.

use std::collections::hash_map::RandomState;
use std::ffi::OsString;
use std::fs;
use std::hash::{BuildHasher, Hasher};
use std::io::ErrorKind;
use std::path::{Path, PathBuf};
use std::process::{Command, ExitStatus, Stdio};

/// The PyPI release both comparisons run against.
pub(crate) const ORACLE_VERSION: &str = "1.5.2";

/// The version check the Python scripts ran, unchanged.
const VERSION_PROBE: &str = "import stele_context; print(stele_context.__version__)";

pub(crate) enum OracleError {
    /// An interpreter imports stele_context, but not 1.5.2. Holds that version.
    Version(String),
    /// No interpreter imports stele_context. Holds what each candidate did.
    Missing(String),
}

/// Finds the Python that runs stele-context 1.5.2 and returns its
/// `sys.executable`.
///
/// `STELE_PARITY_PYTHON` wins when set. Otherwise `python3` and `python` are
/// both run, not just looked up: on Windows `python3` is often a Store stub
/// that is on PATH and fails. Of those that print 1.5.2, the first that can
/// also `import pymupdf` is used, else the first.
pub(crate) fn find_oracle() -> Result<String, OracleError> {
    let pinned = std::env::var_os("STELE_PARITY_PYTHON").filter(|value| !value.is_empty());
    let candidates: Vec<OsString> = match &pinned {
        Some(value) => vec![value.clone()],
        None => vec!["python3".into(), "python".into()],
    };
    let mut viable = Vec::new();
    let mut other_version = None;
    let mut notes = Vec::new();
    for candidate in &candidates {
        let label = candidate.to_string_lossy();
        match probe(candidate) {
            Ok(version) if version == ORACLE_VERSION => viable.push(candidate),
            Ok(version) => {
                notes.push(format!("{label} has stele-context {version}"));
                other_version.get_or_insert(version);
            }
            Err(why) => notes.push(format!("{label}: {why}")),
        }
    }
    let chosen = viable
        .iter()
        .find(|candidate| runs(candidate, "import pymupdf"))
        .or(viable.first());
    if let Some(candidate) = chosen {
        return Ok(executable(candidate));
    }
    if let Some(version) = other_version {
        return Err(OracleError::Version(version));
    }
    let source = if pinned.is_some() {
        "STELE_PARITY_PYTHON"
    } else {
        "python3 or python on PATH"
    };
    Err(OracleError::Missing(format!(
        "no Python oracle: need PyPI stele-context {ORACLE_VERSION} from {source} ({}). \
         Install it with `pip install 'stele-context[mcp]=={ORACLE_VERSION}' pymupdf`, \
         or set STELE_PARITY_PYTHON to that interpreter.",
        notes.join("; ")
    )))
}

fn probe(python: &OsString) -> Result<String, String> {
    let output = Command::new(python)
        .args(["-c", VERSION_PROBE])
        .stdin(Stdio::null())
        .output()
        .map_err(|err| format!("not runnable ({err})"))?;
    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        let stdout = String::from_utf8_lossy(&output.stdout);
        let last = stderr
            .lines()
            .chain(stdout.lines())
            .filter(|line| !line.trim().is_empty())
            .last()
            .unwrap_or("")
            .trim()
            .to_string();
        return Err(format!("exit {}: {last}", returncode(output.status)));
    }
    Ok(String::from_utf8_lossy(&output.stdout).trim().to_string())
}

fn runs(python: &OsString, code: &str) -> bool {
    Command::new(python)
        .args(["-c", code])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .map(|status| status.success())
        .unwrap_or(false)
}

/// The scripts ran the oracle as `sys.executable`: an absolute path.
fn executable(python: &OsString) -> String {
    let fallback = || python.to_string_lossy().into_owned();
    let Ok(output) = Command::new(python)
        .args([
            "-c",
            "import sys; sys.stdout.buffer.write(sys.executable.encode('utf-8'))",
        ])
        .stdin(Stdio::null())
        .output()
    else {
        return fallback();
    };
    match String::from_utf8(output.stdout) {
        Ok(path) if output.status.success() && Path::new(&path).is_file() => path,
        _ => fallback(),
    }
}

/// `Popen.returncode`: the exit code, unsigned on Windows, `-signal` on Unix.
pub(crate) fn returncode(status: ExitStatus) -> i64 {
    #[cfg(unix)]
    {
        use std::os::unix::process::ExitStatusExt;
        if let Some(signal) = status.signal() {
            return -i64::from(signal);
        }
    }
    match status.code() {
        Some(code) if cfg!(windows) => i64::from(code as u32),
        Some(code) => i64::from(code),
        None => -1,
    }
}

/// `repr(str)`: single quotes unless the text has a single quote and no
/// double quote, and Python's escapes for the rest.
pub(crate) fn repr_str(text: &str) -> String {
    let quote = if text.contains('\'') && !text.contains('"') {
        '"'
    } else {
        '\''
    };
    let mut out = String::with_capacity(text.len() + 2);
    out.push(quote);
    for ch in text.chars() {
        match ch {
            '\\' => out.push_str("\\\\"),
            '\t' => out.push_str("\\t"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            ch if ch == quote => {
                out.push('\\');
                out.push(ch);
            }
            ch if printable(ch) => out.push(ch),
            ch if (ch as u32) <= 0xff => out.push_str(&format!("\\x{:02x}", ch as u32)),
            ch if (ch as u32) <= 0xffff => out.push_str(&format!("\\u{:04x}", ch as u32)),
            ch => out.push_str(&format!("\\U{:08x}", ch as u32)),
        }
    }
    out.push(quote);
    out
}

/// `repr(list_of_str)`.
pub(crate) fn repr_list<S: AsRef<str>>(items: &[S]) -> String {
    let parts: Vec<String> = items.iter().map(|item| repr_str(item.as_ref())).collect();
    format!("[{}]", parts.join(", "))
}

/// `str.isprintable` for one character. Python's table also marks unassigned
/// code points as unprintable; this covers controls, format characters,
/// separators other than the ASCII space, private use, and noncharacters.
fn printable(ch: char) -> bool {
    let code = ch as u32;
    if code < 0x80 {
        return (0x20..0x7f).contains(&code);
    }
    let hidden = ch.is_control()
        || code & 0xfffe == 0xfffe
        || matches!(
            code,
            0xa0 | 0xad
                | 0x600..=0x605
                | 0x61c
                | 0x6dd
                | 0x70f
                | 0x890..=0x891
                | 0x8e2
                | 0x1680
                | 0x180e
                | 0x2000..=0x200f
                | 0x2028..=0x202f
                | 0x205f..=0x206f
                | 0x3000
                | 0xe000..=0xf8ff
                | 0xfdd0..=0xfdef
                | 0xfeff
                | 0xfff9..=0xfffb
                | 0x110bd
                | 0x110cd
                | 0x13430..=0x1343f
                | 0x1bca0..=0x1bca3
                | 0x1d173..=0x1d17a
                | 0xe0001
                | 0xe0020..=0xe007f
                | 0xf0000..=0x10ffff
        );
    !hidden
}

/// `tempfile.mkdtemp(prefix=...)`, removed again on drop like
/// `shutil.rmtree(path, ignore_errors=True)`.
pub(crate) struct TempDir {
    path: PathBuf,
}

impl TempDir {
    pub(crate) fn new(prefix: &str) -> Result<TempDir, String> {
        const LETTERS: &[u8] = b"abcdefghijklmnopqrstuvwxyz0123456789_";
        let base = std::env::temp_dir();
        let state = RandomState::new();
        for attempt in 0..10_000u32 {
            let mut hasher = state.build_hasher();
            hasher.write_u32(attempt);
            hasher.write_u32(std::process::id());
            let mut bits = hasher.finish();
            let mut name = String::from(prefix);
            for _ in 0..8 {
                name.push(LETTERS[(bits % LETTERS.len() as u64) as usize] as char);
                bits /= LETTERS.len() as u64;
            }
            let path = base.join(name);
            match private_dir().create(&path) {
                Ok(()) => return Ok(TempDir { path }),
                Err(err) if err.kind() == ErrorKind::AlreadyExists => continue,
                Err(err) => return Err(format!("mkdtemp {}: {err}", path.display())),
            }
        }
        Err(format!(
            "no usable temporary directory name under {}",
            base.display()
        ))
    }

    pub(crate) fn path(&self) -> &Path {
        &self.path
    }
}

/// mkdtemp creates the directory with mode 0700.
fn private_dir() -> fs::DirBuilder {
    #[cfg(unix)]
    {
        use std::os::unix::fs::DirBuilderExt;
        let mut builder = fs::DirBuilder::new();
        builder.mode(0o700);
        builder
    }
    #[cfg(not(unix))]
    {
        fs::DirBuilder::new()
    }
}

impl Drop for TempDir {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.path);
    }
}
