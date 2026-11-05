//! Test-only lookup of the Python 1.5.2 reference interpreter.
//!
//! The release binaries never start Python. Tests compare against the
//! unmodified PyPI `stele-context==1.5.2` and need one interpreter for it.
//! `STELE_PARITY_PYTHON` names it. Without that variable the lookup tries
//! `python3` and then `python`, and keeps the first one that actually runs.
//! On Windows `python3` is often the Microsoft Store alias, which exists on
//! PATH but exits with an error, so "found on PATH" is not enough.

use std::process::Command;
use std::sync::OnceLock;

fn runs(bin: &str, code: &str) -> Option<String> {
    let out = Command::new(bin).args(["-c", code]).output().ok()?;
    if !out.status.success() {
        return None;
    }
    Some(String::from_utf8_lossy(&out.stdout).trim().to_string())
}

fn candidates() -> Vec<String> {
    if let Ok(chosen) = std::env::var("STELE_PARITY_PYTHON") {
        if !chosen.trim().is_empty() {
            return vec![chosen];
        }
    }
    vec!["python3".to_string(), "python".to_string()]
}

/// An interpreter with stele-context 1.5.2 installed. Panics with the fix
/// when there is none: a parity test that cannot reach its oracle fails.
pub fn reference() -> &'static str {
    static FOUND: OnceLock<String> = OnceLock::new();
    FOUND.get_or_init(|| {
        for bin in candidates() {
            let version = runs(
                &bin,
                "import stele_context; print(stele_context.__version__)",
            );
            if version.as_deref() == Some("1.5.2") {
                return bin;
            }
        }
        panic!(
            "no Python with stele-context==1.5.2 found (tried {:?}); \
             pip install 'stele-context[mcp]==1.5.2' 'pymupdf>=1.23,<2' \
             or set STELE_PARITY_PYTHON",
            candidates()
        )
    })
}

/// Any working interpreter, for tests that only need the standard library
/// (zlib, sqlite3).
pub fn plain() -> &'static str {
    static FOUND: OnceLock<String> = OnceLock::new();
    FOUND.get_or_init(|| {
        for bin in candidates() {
            if runs(&bin, "import sys").is_some() {
                return bin;
            }
        }
        panic!(
            "no working Python found (tried {:?}); set STELE_PARITY_PYTHON",
            candidates()
        )
    })
}
