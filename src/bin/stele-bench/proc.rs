//! Wall-clock timing for a child process, and a short MCP handshake.

use crate::jsonutil::{as_array, id_is, object_get};
use std::collections::HashMap;
use std::io::{BufRead, BufReader, Read, Write};
use std::path::Path;
use std::process::{Command, Stdio};
use std::sync::mpsc;
use std::thread;
use std::time::{Duration, Instant};
use stele_context::json::{self, Value};

pub(crate) struct Sample {
    pub wall_ms: f64,
    pub out_bytes: usize,
    pub rc: i32,
    pub stdout_tail: String,
    pub stderr_tail: String,
}

pub(crate) fn base_env() -> HashMap<String, String> {
    let mut env: HashMap<String, String> = std::env::vars().collect();
    env.remove("PYTHONPATH");
    env.remove("STELE_CONTEXT_STORAGE_DIR");
    env.insert("STELE_MCP_MODE".to_string(), "lite".to_string());
    env
}

pub(crate) fn run_command(
    argv: &[String],
    cwd: &Path,
    env: &HashMap<String, String>,
    delay: Duration,
) -> Result<Sample, String> {
    let started = Instant::now();
    if !delay.is_zero() {
        thread::sleep(delay);
    }
    let mut child = Command::new(&argv[0])
        .args(&argv[1..])
        .current_dir(cwd)
        .env_clear()
        .envs(env)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|err| format!("spawn {}: {err}", argv[0]))?;
    let stdout = child.stdout.take();
    let stderr = child.stderr.take();
    let out_handle = thread::spawn(move || read_pipe(stdout));
    let err_handle = thread::spawn(move || read_pipe(stderr));
    let status = child
        .wait()
        .map_err(|err| format!("wait {}: {err}", argv[0]))?;
    let out = out_handle.join().unwrap_or_default();
    let err = err_handle.join().unwrap_or_default();
    Ok(Sample {
        wall_ms: started.elapsed().as_secs_f64() * 1000.0,
        out_bytes: out.len(),
        rc: status.code().unwrap_or(1),
        stdout_tail: tail(&out, 1500),
        stderr_tail: tail(&err, 1500),
    })
}

pub(crate) fn run_ok(
    argv: &[String],
    cwd: &Path,
    env: &HashMap<String, String>,
    delay: Duration,
) -> Result<f64, String> {
    let sample = run_command(argv, cwd, env, delay)?;
    if sample.rc != 0 {
        return Err(format!(
            "{argv:?} exited {}\n{}\n{}",
            sample.rc, sample.stdout_tail, sample.stderr_tail
        ));
    }
    Ok(sample.wall_ms)
}

pub(crate) fn mcp_handshake(command: &Path) -> Result<(f64, f64, usize), String> {
    let storage = std::env::temp_dir().join(format!("stele-gate-mcp-{}", std::process::id()));
    let empty = std::env::temp_dir().join(format!(
        "stele-gate-empty-{}-{}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_nanos())
            .unwrap_or(0)
    ));
    let _ = std::fs::remove_dir_all(&storage);
    let _ = std::fs::remove_dir_all(&empty);
    std::fs::create_dir_all(&storage).map_err(|err| err.to_string())?;
    std::fs::create_dir_all(&empty).map_err(|err| err.to_string())?;
    let mut env = base_env();
    env.insert(
        "STELE_CONTEXT_STORAGE_DIR".to_string(),
        storage.display().to_string(),
    );
    let result = mcp_session(command, &empty, &env);
    let _ = std::fs::remove_dir_all(&storage);
    let _ = std::fs::remove_dir_all(&empty);
    result
}

fn mcp_session(
    command: &Path,
    cwd: &Path,
    env: &HashMap<String, String>,
) -> Result<(f64, f64, usize), String> {
    let mut child = Command::new(command)
        .current_dir(cwd)
        .env_clear()
        .envs(env)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|err| format!("spawn mcp: {err}"))?;
    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| "mcp stdout closed".to_string())?;
    let stderr = child.stderr.take();
    let mut stdin = child
        .stdin
        .take()
        .ok_or_else(|| "mcp stdin closed".to_string())?;
    let (tx, rx) = mpsc::channel::<String>();
    thread::spawn(move || {
        let reader = BufReader::new(stdout);
        for line in reader.lines() {
            let Ok(line) = line else { break };
            if tx.send(line).is_err() {
                break;
            }
        }
    });
    thread::spawn(move || {
        if let Some(mut stderr) = stderr {
            let mut sink = Vec::new();
            let _ = stderr.read_to_end(&mut sink);
        }
    });
    let started = Instant::now();
    send(
        &mut stdin,
        &Value::object(vec![
            ("jsonrpc", Value::string("2.0")),
            ("id", Value::Int(1)),
            ("method", Value::string("initialize")),
            (
                "params",
                Value::object(vec![
                    ("protocolVersion", Value::string("2025-11-25")),
                    ("capabilities", Value::object(vec![])),
                    (
                        "clientInfo",
                        Value::object(vec![
                            ("name", Value::string("stele-benchmark-gate")),
                            ("version", Value::string("0")),
                        ]),
                    ),
                ]),
            ),
        ]),
    )?;
    read_id(&rx, &mut child, 1, Duration::from_secs(60))?;
    let initialize_ms = started.elapsed().as_secs_f64() * 1000.0;
    send(
        &mut stdin,
        &Value::object(vec![
            ("jsonrpc", Value::string("2.0")),
            ("method", Value::string("notifications/initialized")),
        ]),
    )?;
    let listed_at = Instant::now();
    send(
        &mut stdin,
        &Value::object(vec![
            ("jsonrpc", Value::string("2.0")),
            ("id", Value::Int(2)),
            ("method", Value::string("tools/list")),
            ("params", Value::object(vec![])),
        ]),
    )?;
    let listed = read_id(&rx, &mut child, 2, Duration::from_secs(30))?;
    let list_ms = listed_at.elapsed().as_secs_f64() * 1000.0;
    let tools = object_get(&listed, "tools").and_then(as_array);
    if tools.map(|items| items.is_empty()).unwrap_or(true) {
        let _ = child.kill();
        return Err("tools/list returned no tools".to_string());
    }
    let bytes = json::to_string(&listed).len();
    drop(stdin);
    let _ = child.wait();
    Ok((initialize_ms, list_ms, bytes))
}

fn send(stdin: &mut impl Write, message: &Value) -> Result<(), String> {
    stdin
        .write_all(json::to_string(message).as_bytes())
        .and_then(|_| stdin.write_all(b"\n"))
        .and_then(|_| stdin.flush())
        .map_err(|err| format!("mcp write: {err}"))
}

fn read_id(
    rx: &mpsc::Receiver<String>,
    child: &mut std::process::Child,
    request_id: i64,
    timeout: Duration,
) -> Result<Value, String> {
    let deadline = Instant::now() + timeout;
    loop {
        let remaining = deadline.saturating_duration_since(Instant::now());
        if remaining.is_zero() {
            let _ = child.kill();
            return Err(format!("timed out waiting for mcp id={request_id}"));
        }
        match rx.recv_timeout(remaining) {
            Ok(line) => {
                let line = line.trim();
                if line.is_empty() {
                    continue;
                }
                let message =
                    json::parse(line).map_err(|err| format!("mcp json: {}", err.message))?;
                if !id_is(&message, request_id) {
                    continue;
                }
                if object_get(&message, "error").is_some() {
                    let _ = child.kill();
                    return Err(format!("mcp error: {line}"));
                }
                return object_get(&message, "result")
                    .cloned()
                    .ok_or_else(|| format!("mcp result missing: {line}"));
            }
            Err(mpsc::RecvTimeoutError::Timeout) => {
                let _ = child.kill();
                return Err(format!("timed out waiting for mcp id={request_id}"));
            }
            Err(mpsc::RecvTimeoutError::Disconnected) => {
                let status = child.wait().ok();
                return Err(format!("mcp exited before id={request_id}: {status:?}"));
            }
        }
    }
}

fn read_pipe(pipe: Option<impl Read>) -> Vec<u8> {
    let Some(mut pipe) = pipe else {
        return Vec::new();
    };
    let mut buf = Vec::new();
    let _ = pipe.read_to_end(&mut buf);
    buf
}

fn tail(bytes: &[u8], max: usize) -> String {
    let start = bytes.len().saturating_sub(max);
    String::from_utf8_lossy(&bytes[start..]).into_owned()
}
