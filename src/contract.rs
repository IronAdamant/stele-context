//! Success and error envelopes. Exit 0 is success, 1 is an operation error,
//! 2 is usage.

use crate::json::{self, Value};
use crate::SCHEMA_VERSION;
use std::io::{self, Write};

pub const EXIT_OK: i32 = 0;
pub const EXIT_ERROR: i32 = 1;
pub const EXIT_USAGE: i32 = 2;

#[derive(Clone, Debug)]
pub struct ErrorBody {
    pub code: &'static str,
    pub message: String,
    pub hint: String,
    pub retryable: bool,
}

impl ErrorBody {
    pub fn new(
        code: &'static str,
        message: impl Into<String>,
        hint: impl Into<String>,
    ) -> ErrorBody {
        ErrorBody {
            code,
            message: message.into(),
            hint: hint.into(),
            retryable: false,
        }
    }
}

pub struct Response {
    pub code: i32,
    pub stdout: Vec<u8>,
}

impl Response {
    pub fn json(code: i32, value: &Value) -> Response {
        let mut bytes = json::to_vec(value);
        bytes.push(b'\n');
        Response {
            code,
            stdout: bytes,
        }
    }

    pub fn text(code: i32, text: &str) -> Response {
        let mut bytes = text.as_bytes().to_vec();
        if !bytes.ends_with(b"\n") {
            bytes.push(b'\n');
        }
        Response {
            code,
            stdout: bytes,
        }
    }

    pub fn success(command: &str, result: Value) -> Response {
        Response::json(
            EXIT_OK,
            &Value::object(vec![
                ("ok", Value::Bool(true)),
                ("schema_version", Value::string(SCHEMA_VERSION)),
                ("command", Value::string(command)),
                ("result", result),
            ]),
        )
    }

    pub fn failure(command: &str, code: i32, error: ErrorBody) -> Response {
        Response::json(
            code,
            &Value::object(vec![
                ("ok", Value::Bool(false)),
                ("schema_version", Value::string(SCHEMA_VERSION)),
                ("command", Value::string(command)),
                (
                    "error",
                    Value::object(vec![
                        ("code", Value::string(error.code)),
                        ("message", Value::string(error.message)),
                        ("hint", Value::string(error.hint)),
                        ("retryable", Value::Bool(error.retryable)),
                    ]),
                ),
            ]),
        )
    }

    /// Write stdout. A closed pipe is success: agents close pipes on purpose.
    pub fn emit(self) -> i32 {
        match io::stdout().write_all(&self.stdout) {
            Ok(()) => {
                let _ = io::stdout().flush();
                self.code
            }
            Err(err) if err.kind() == io::ErrorKind::BrokenPipe => EXIT_OK,
            Err(err) => {
                let _ = writeln!(io::stderr(), "stdout: {err}");
                EXIT_ERROR
            }
        }
    }
}
