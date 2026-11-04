//! Full MCP stdio compare against Python 1.5.2. See scripts/mcp_parity.py.

use std::path::PathBuf;
use std::process::Command;

#[test]
fn mcp_full_output_matches_python_152() {
    let bin = env!("CARGO_BIN_EXE_stele-context");
    let repo = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let script = repo.join("scripts/mcp_parity.py");
    let ran = Command::new("python3")
        .arg(&script)
        .arg("--binary")
        .arg(bin)
        .current_dir(&repo)
        .output()
        .expect("python3");
    let stdout = String::from_utf8_lossy(&ran.stdout);
    let stderr = String::from_utf8_lossy(&ran.stderr);
    assert!(
        ran.status.success(),
        "mcp parity failed\n{stdout}\n{stderr}"
    );
    assert!(
        stdout.contains("undocumented diffs: 0"),
        "missing parity line\n{stdout}"
    );
}
