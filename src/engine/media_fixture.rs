use super::Engine;
use std::fs;
use std::path::PathBuf;
use std::time::{SystemTime, UNIX_EPOCH};

#[test]
fn media_fixture_matches_the_expected_counts() {
    let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let root = manifest.join("parity/fixtures/media");
    let expected = fs::read_to_string(manifest.join("parity/goldens/media.json")).unwrap();
    let store = std::env::temp_dir().join(format!(
        "stele-media-{}",
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    ));
    let _ = fs::remove_dir_all(&store);
    let mut engine = Engine::open_at(root, store.clone()).unwrap();
    engine.index(&[".".to_string()], false).unwrap();
    let stats = engine.stats();
    let storage = match &stats {
        crate::json::Value::Object(pairs) => pairs
            .iter()
            .find(|(key, _)| key == "storage")
            .map(|(_, value)| value),
        _ => None,
    };
    let storage = storage.expect("storage");
    for (key, label) in [
        ("document_count", "documents"),
        ("chunk_count", "chunks"),
        ("symbol_count", "symbols"),
    ] {
        let value = crate::store::int_field(storage, key);
        assert!(
            expected.contains(&format!("\"{label}\": {value}")),
            "{label}={value} not in {expected}"
        );
    }
    let found = engine.search("Hello PDF symbols", "keyword", 3);
    assert!(crate::json::to_string(&found).contains("note.pdf"));
    let _ = fs::remove_dir_all(&store);
}
