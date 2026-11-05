//! `serve`: GET /tools, GET /health, POST /call.
//!
//! The envelope matches Python 1.5.2: `{"success","result"}` or `{"error"}`.
//! One thread per connection, `std::net` only.

use crate::engine::Engine;
use crate::json::{self, Value};
use crate::mcp;
use crate::VERSION;
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::thread;

pub fn listen(engine: Engine, host: &str, port: u16) -> Result<(), String> {
    let listener = TcpListener::bind((host, port)).map_err(|err| format!("bind: {err}"))?;
    serve_listener(engine, listener)
}

pub fn serve_listener(engine: Engine, listener: TcpListener) -> Result<(), String> {
    let addr = listener
        .local_addr()
        .map_err(|err| format!("addr: {err}"))?;
    eprintln!("stele-context serve http://{addr}");
    let engine = std::sync::Arc::new(std::sync::Mutex::new(engine));
    for conn in listener.incoming() {
        let Ok(stream) = conn else { continue };
        let engine = std::sync::Arc::clone(&engine);
        thread::spawn(move || {
            let _ = handle_connection(&engine, stream);
        });
    }
    Ok(())
}

fn handle_connection(
    engine: &std::sync::Mutex<Engine>,
    mut stream: TcpStream,
) -> std::io::Result<()> {
    let _ = stream.set_read_timeout(Some(std::time::Duration::from_secs(5)));
    let text = read_request(&mut stream)?;
    let response = answer(engine, &text);
    stream.write_all(response.as_bytes())?;
    Ok(())
}

fn read_request(stream: &mut TcpStream) -> std::io::Result<String> {
    let mut buf = Vec::new();
    let mut tmp = [0u8; 4096];
    loop {
        let n = stream.read(&mut tmp)?;
        if n == 0 {
            break;
        }
        buf.extend_from_slice(&tmp[..n]);
        if let Some(end) = find_sep(&buf) {
            let headers = String::from_utf8_lossy(&buf[..end]);
            let need = end + 4 + content_length(&headers);
            if buf.len() >= need {
                break;
            }
        }
        if buf.len() > 2_000_000 {
            break;
        }
    }
    Ok(String::from_utf8_lossy(&buf).into_owned())
}

fn find_sep(buf: &[u8]) -> Option<usize> {
    buf.windows(4).position(|window| window == b"\r\n\r\n")
}

fn content_length(headers: &str) -> usize {
    headers
        .lines()
        .find_map(|line| {
            let (name, value) = line.split_once(':')?;
            if name.eq_ignore_ascii_case("content-length") {
                value.trim().parse().ok()
            } else {
                None
            }
        })
        .unwrap_or(0)
}

fn answer(engine: &std::sync::Mutex<Engine>, request: &str) -> String {
    let mut lines = request.lines();
    let Some(start) = lines.next() else {
        return http(
            400,
            &json::to_string(&Value::object(vec![("error", Value::string("empty"))])),
        );
    };
    let mut parts = start.split_whitespace();
    let method = parts.next().unwrap_or("");
    let path = parts.next().unwrap_or("");
    let body = request.split("\r\n\r\n").nth(1).unwrap_or("");
    let (status, payload) = match (method, path) {
        ("GET", "/health") => (200, health(engine)),
        ("GET", "/tools") => (200, tools()),
        ("POST", "/call") => call(engine, body),
        _ => (
            404,
            Value::object(vec![("error", Value::string("Not found"))]),
        ),
    };
    http(status, &json::to_string(&payload))
}

fn health(engine: &std::sync::Mutex<Engine>) -> Value {
    let Ok(engine) = engine.lock() else {
        return Value::object(vec![("error", Value::string("lock"))]);
    };
    Value::object(vec![
        ("status", Value::string("healthy")),
        ("version", Value::string(VERSION)),
        ("storage", engine.storage_report()),
    ])
}

fn tools() -> Value {
    mcp::tools_document()
}

fn call(engine: &std::sync::Mutex<Engine>, body: &str) -> (u16, Value) {
    let Ok(request) = json::parse(body) else {
        return (
            400,
            Value::object(vec![("error", Value::string("Invalid JSON"))]),
        );
    };
    let tool = match field(&request, "tool") {
        Some(Value::String(name)) if !name.is_empty() => name.clone(),
        _ => {
            return (
                400,
                Value::object(vec![("error", Value::string("Missing 'tool' field"))]),
            )
        }
    };
    let params = field(&request, "parameters")
        .cloned()
        .unwrap_or(Value::object(vec![]));
    let Ok(mut engine) = engine.lock() else {
        return (500, Value::object(vec![("error", Value::string("lock"))]));
    };
    if !known_tool(&tool) {
        let available = mcp::lite_tools()
            .iter()
            .copied()
            .map(Value::string)
            .collect();
        return (
            200,
            Value::object(vec![
                ("error", Value::string(format!("Unknown tool: {tool}"))),
                ("available_tools", Value::Array(available)),
            ]),
        );
    }
    // 1.5.2 `execute_tool`: a TypeError from binding is "Invalid
    // parameters", any other exception is "Tool execution failed" with the
    // self-healing hint, and a result that needs guidance carries the hint
    // both on the envelope and inside the result.
    if let Err(message) = mcp::bind_args(&tool, &params, true) {
        return (
            200,
            Value::object(vec![(
                "error",
                Value::string(format!("Invalid parameters for {tool}: {message}")),
            )]),
        );
    }
    match mcp::dispatch(&mut engine, &tool, &params) {
        Ok(mut result) => {
            let hint = mcp::result_hint(&tool, &result);
            let mut pairs = vec![("success".to_string(), Value::Bool(true))];
            if let Some(hint) = hint {
                if let Value::Object(fields) = &mut result {
                    if !fields.iter().any(|(key, _)| key == "hint") {
                        fields.push(("hint".to_string(), Value::string(hint)));
                    }
                }
                pairs.push(("result".to_string(), result));
                pairs.push(("hint".to_string(), Value::string(hint)));
            } else {
                pairs.push(("result".to_string(), result));
            }
            (200, Value::Object(pairs))
        }
        Err(message) => {
            let mut pairs = vec![(
                "error".to_string(),
                Value::string(format!("Tool execution failed: {message}")),
            )];
            if let Some(hint) = mcp::healing_hint(&tool, &message.to_lowercase()) {
                pairs.push(("hint".to_string(), Value::string(hint)));
            }
            (200, Value::Object(pairs))
        }
    }
}

fn known_tool(name: &str) -> bool {
    mcp::tool_known(name)
}

fn http(status: u16, body: &str) -> String {
    let reason = if status == 200 { "OK" } else { "Error" };
    format!(
        "HTTP/1.1 {status} {reason}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nAccess-Control-Allow-Origin: *\r\nConnection: close\r\n\r\n{body}",
        body.len()
    )
}

fn field<'a>(value: &'a Value, key: &str) -> Option<&'a Value> {
    let Value::Object(pairs) = value else {
        return None;
    };
    pairs
        .iter()
        .find(|(name, _)| name == key)
        .map(|(_, item)| item)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::net::TcpListener;

    #[test]
    fn health_tools_and_stats_round_trip() {
        let dir = std::env::temp_dir().join(format!("stele-http-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        let engine = Engine::open(Some(&dir)).unwrap();
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        thread::spawn(move || {
            let _ = serve_listener(engine, listener);
        });
        let health = request(port, "GET /health HTTP/1.1\r\nHost: localhost\r\n\r\n");
        assert!(health.contains("healthy"), "{health}");
        let health_body = json::parse(http_body(&health)).unwrap();
        let storage = field(&health_body, "storage").unwrap();
        for key in STORAGE_KEYS {
            assert!(field(storage, key).is_some(), "missing storage.{key}");
        }
        let tools = request(port, "GET /tools HTTP/1.1\r\nHost: localhost\r\n\r\n");
        let tools_body = json::parse(http_body(&tools)).unwrap();
        let Value::Array(rows) = field(&tools_body, "tools").unwrap() else {
            panic!("tools");
        };
        assert_eq!(rows.len(), mcp::lite_tools().len());
        for row in rows {
            assert!(field(row, "name").is_some());
            assert!(field(row, "description").is_some(), "{row:?}");
            let params = field(row, "parameters").unwrap();
            assert!(matches!(field(params, "type"), Some(Value::String(kind)) if kind == "object"));
        }
        let unknown = request(
            port,
            "POST /call HTTP/1.1\r\nHost: localhost\r\nContent-Length: 32\r\n\r\n{\"tool\":\"stats\",\"parameters\":{}}",
        );
        assert!(unknown.contains("Unknown tool"), "{unknown}");
        let body = "{\"tool\":\"doctor\",\"parameters\":{}}";
        let call = request(
            port,
            &format!(
                "POST /call HTTP/1.1\r\nHost: localhost\r\nContent-Length: {}\r\n\r\n{body}",
                body.len()
            ),
        );
        assert!(call.contains("success"), "{call}");
        let _ = std::fs::remove_dir_all(&dir);
    }

    const STORAGE_KEYS: &[&str] = &[
        "chunk_count",
        "document_count",
        "session_count",
        "total_tokens",
        "version_count",
        "annotation_count",
        "history_count",
        "kv_cache_size_bytes",
        "database_size_bytes",
        "storage_dir",
        "symbol_count",
        "definition_count",
        "reference_count",
        "edge_count",
        "runtime_symbol_count",
        "locked_documents",
        "expired_locks",
        "active_lock_agents",
        "total_conflicts",
        "last_conflict_at",
    ];

    fn http_body(response: &str) -> &str {
        response.split("\r\n\r\n").nth(1).unwrap_or("")
    }

    fn request(port: u16, message: &str) -> String {
        let mut stream = TcpStream::connect(("127.0.0.1", port)).unwrap();
        stream.write_all(message.as_bytes()).unwrap();
        let mut buf = String::new();
        stream.read_to_string(&mut buf).unwrap();
        buf
    }
}
