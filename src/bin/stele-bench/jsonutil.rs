use stele_context::json::Value;

pub(crate) fn object_get<'a>(value: &'a Value, key: &str) -> Option<&'a Value> {
    match value {
        Value::Object(pairs) => pairs.iter().find(|(name, _)| name == key).map(|(_, v)| v),
        _ => None,
    }
}

pub(crate) fn as_str(value: &Value) -> Option<&str> {
    match value {
        Value::String(text) => Some(text),
        _ => None,
    }
}

pub(crate) fn as_f64(value: &Value) -> Option<f64> {
    match value {
        Value::Float(number) => Some(*number),
        Value::Int(number) => Some(*number as f64),
        _ => None,
    }
}

pub(crate) fn as_array(value: &Value) -> Option<&[Value]> {
    match value {
        Value::Array(items) => Some(items),
        _ => None,
    }
}

pub(crate) fn id_is(value: &Value, want: i64) -> bool {
    match object_get(value, "id") {
        Some(Value::Int(number)) => *number == want,
        Some(Value::Float(number)) => *number == want as f64,
        _ => false,
    }
}
