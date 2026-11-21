//! `find_references` (2.3 to 2.5): definition rows, reference rows, Rust
//! word matches, struct fields grouped by owning type (2.5) and the
//! `named_in` section: files of other languages and string literals that
//! name the symbol (2.5).

use super::query::language_of;
use super::Engine;
use crate::json::Value;
use crate::store::{int_field, string_field, Chunk};
use crate::symbols::RustUse;
use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};

/// Field rows (`field_access`, `struct_field`, and `mention` rows of a
/// name with field definitions) `find_references` lists unless asked for
/// more; the rest are counted.
pub const FIELD_ROWS: usize = 50;
/// Files `named_in` lists, and lines it lists per file.
const NAMED_FILES: usize = 10;
const NAMED_LINES: usize = 3;
/// Owning types a `fields.note` names before it points to `owners`.
const NOTE_TYPES: usize = 8;
/// Shorter names are not scanned for `named_in`.
const NAMED_MIN_CHARS: usize = 4;

/// What `find_references_with` returns besides the plain lookup (2.5).
#[derive(Clone, Debug, Default)]
pub struct RefOptions {
    /// List references in every language (2.3).
    pub all_languages: bool,
    /// Keep only the definitions and field rows of this owning type.
    pub owner: Option<String>,
    /// Field rows listed at most: `None` is `FIELD_ROWS`, `Some(0)` all.
    pub field_limit: Option<usize>,
    /// `named_in`: `None` scans names that are not one plain word,
    /// `Some(true)` any name, `Some(false)` none (no section).
    pub named_in: Option<bool>,
}

fn is_field_use(kind: &str) -> bool {
    kind == "field_access" || kind == "struct_field"
}

fn row_key(row: &Value) -> (String, i64) {
    (
        string_field(row, "document_path"),
        int_field(row, "line_number"),
    )
}

fn set_field(row: &mut Value, key: &str, value: Value) {
    if let Value::Object(pairs) = row {
        if let Some(slot) = pairs.iter_mut().find(|(name, _)| name == key) {
            slot.1 = value;
        } else {
            pairs.push((key.to_string(), value));
        }
    }
}

impl Engine {
    pub fn find_references(&self, name: &str) -> Value {
        self.find_references_in(name, false)
    }

    /// 2.3: references are kept to the languages the symbol is defined in
    /// (a Rust `crc32` does not list Python's `zlib.crc32` calls) unless
    /// `all_languages`. A symbol with no definition, or one defined in a
    /// file of no known language, is not narrowed.
    pub fn find_references_in(&self, name: &str, all_languages: bool) -> Value {
        self.find_references_with(
            name,
            &RefOptions {
                all_languages,
                ..RefOptions::default()
            },
        )
    }

    pub fn find_references_with(&self, name: &str, options: &RefOptions) -> Value {
        let mut defs = self.reference_rows(name, true);
        let mut refs = self.reference_rows(name, false);
        let fields = defs.iter().any(|row| string_field(row, "kind") == "field");
        let owner = options.owner.as_deref().filter(|owner| !owner.is_empty());
        let mut other_owner_defs = 0usize;
        let mut owner_miss = None;
        if let Some(owner) = owner {
            let before = defs.len();
            let all_owners: BTreeSet<String> = defs
                .iter()
                .map(|row| string_field(row, "owner"))
                .filter(|owner| !owner.is_empty())
                .collect();
            defs.retain(|row| string_field(row, "owner") == owner);
            other_owner_defs = before - defs.len();
            if defs.is_empty() {
                owner_miss = Some(if all_owners.is_empty() {
                    format!("No type has a field {name}; owner keeps one type's field rows. Call again without owner.")
                } else {
                    format!(
                        "owner '{owner}' has no field {name}. Types with one: {}. Call again with one of them or without owner.",
                        all_owners.into_iter().collect::<Vec<_>>().join(", ")
                    )
                });
            }
        }
        let languages: Vec<&str> = defs
            .iter()
            .map(|row| language_of(&string_field(row, "document_path")))
            .collect();
        let mut omitted = 0usize;
        if !options.all_languages && !languages.is_empty() && !languages.contains(&"") {
            let before = refs.len();
            refs.retain(|row| {
                languages.contains(&language_of(&string_field(row, "document_path")))
            });
            omitted = before - refs.len();
        }
        // 2.4: a Rust symbol also gets every other line of code that names
        // it as a word (comments and strings skipped), so the answer has
        // grep's recall: a call in a chunk that already names it, a fn
        // passed as a value, `Self::name`. The symbol rows give the first
        // line per chunk; these rows give the rest. 2.5: each line also
        // says how it uses the name and, for a field, the owning type
        // where the code writes it.
        let mut uses: HashMap<(String, i64), Vec<RustUse>> = HashMap::new();
        if languages.contains(&"rust") {
            let (extra, found) = self.rust_mentions(name, &defs, &refs, fields);
            uses = found;
            refs.extend(extra);
        }
        let mut field_block = None;
        let mut own = Vec::new();
        if fields {
            field_block = Some(self.group_fields(&mut defs, &mut refs, &uses, owner));
        } else {
            sort_rows(&mut refs);
            // One row per line: two uses on one line are one place.
            let mut lines = HashSet::new();
            refs.retain(|row| lines.insert(row_key(row)));
            own = self.split_own_body(name, &defs, &mut refs);
        }
        // `total` counts references only: not the definitions, not the
        // lines inside the symbol's own body.
        let total = refs.len() as i64;
        let verdict = if defs.is_empty() && refs.is_empty() && own.is_empty() {
            "not_found"
        } else if !defs.is_empty() && refs.is_empty() {
            "unreferenced"
        } else if defs.is_empty() {
            "external"
        } else {
            "referenced"
        };
        let mut named = Value::Null;
        if options.named_in != Some(false) {
            let known: HashSet<(String, i64)> =
                defs.iter().chain(&refs).chain(&own).map(row_key).collect();
            named = self.named_in(name, &languages, &known, options.named_in == Some(true));
        }
        // The field cap applies after the verdict and `total`.
        let mut field_omitted = 0usize;
        if let Some(block) = field_block.as_mut() {
            let limit = options.field_limit.unwrap_or(FIELD_ROWS);
            if limit > 0 {
                let mut shown = 0usize;
                refs.retain(|row| {
                    // A field's word matches (a local of the same name) are
                    // capped with its field uses.
                    let kind = string_field(row, "kind");
                    if !is_field_use(&kind) && kind != "mention" {
                        return true;
                    }
                    shown += 1;
                    shown <= limit
                });
                field_omitted = shown.saturating_sub(limit);
            }
            if field_omitted > 0 {
                if let Value::Object(pairs) = block {
                    pairs.push(("omitted".to_string(), Value::Int(field_omitted as i64)));
                    pairs.push((
                        "hint".to_string(),
                        Value::string(format!(
                            "{field_omitted} field and mention rows not listed: pass owner=<Type> for one type's rows, or full=true (CLI --limit 0) for all."
                        )),
                    ));
                }
            }
        }
        let own_count = own.len();
        let not_found = verdict == "not_found";
        let mut out = vec![
            ("symbol", Value::string(name)),
            ("verdict", Value::string(verdict)),
            ("definitions", Value::Array(defs)),
            ("references", Value::Array(refs)),
            ("total", Value::Int(total)),
        ];
        if own_count > 0 {
            out.push(("self_references", Value::Array(own)));
        }
        out.extend([
            ("symbol_index", self.symbol_index()),
            (
                "guidance",
                if not_found {
                    Value::string(super::query::guidance_empty(&self.symbol_index()))
                } else {
                    Value::Null
                },
            ),
        ]);
        if own_count > 0 && verdict == "unreferenced" {
            out.push((
                "self_note",
                Value::string(format!(
                    "{name} is named only inside its own definition ({own_count} {}: recursion or its own type); nothing else uses it.",
                    if own_count == 1 { "line" } else { "lines" }
                )),
            ));
        }
        if omitted > 0 {
            out.push(("other_language_references", Value::Int(omitted as i64)));
        }
        if let Some(block) = field_block {
            out.push(("fields", block));
        }
        if other_owner_defs > 0 {
            out.push((
                "other_owner_definitions",
                Value::Int(other_owner_defs as i64),
            ));
        }
        if let Some(miss) = owner_miss {
            out.push(("error", Value::string(miss)));
        }
        let named_lines = int_field(&named, "lines_total");
        if !matches!(named, Value::Null) {
            out.push(("named_in", named));
        }
        if named_lines > 0 && (verdict == "unreferenced" || verdict == "not_found") {
            out.push((
                "note",
                Value::string(format!(
                    "No code uses {name}, but {named_lines} lines of other files or string literals name it (named_in): a script, a doc or a dispatch by string may reach it."
                )),
            ));
        }
        Value::object(out)
    }

    /// Move the reference rows inside the one definition's own body (and
    /// on its line) out of `refs`; returns them. With several definitions
    /// a use inside one may reach another, so nothing moves.
    fn split_own_body(&self, name: &str, defs: &[Value], refs: &mut Vec<Value>) -> Vec<Value> {
        let [def] = defs else {
            return Vec::new();
        };
        let dynamic = matches!(def, Value::Object(pairs) if pairs.iter().any(|(key, value)| key == "dynamic" && matches!(value, Value::Bool(true))));
        if dynamic {
            return Vec::new();
        }
        let (path, line) = row_key(def);
        let Some(body) = self.own_body(name, &path, line) else {
            return Vec::new();
        };
        let mut own = Vec::new();
        refs.retain(|row| {
            let (row_path, row_line) = row_key(row);
            let scope = self.reference_scope(name, &row_path, row_line);
            let inside =
                (row_path == path && row_line == line) || body.holds(&row_path, row_line, scope);
            if inside {
                own.push(row.clone());
            }
            !inside
        });
        own
    }

    /// 2.5: the field rows of a symbol with field definitions: each field
    /// use gains `owner` (the type the code writes: `self.name` in `impl
    /// Type`, `Type { name }`) or `name_only: true`, and `write: true` when
    /// it changes the field. Rows with an owner come first, grouped by it;
    /// with `owner` only that type's rows stay. Returns the `fields` block.
    fn group_fields(
        &self,
        defs: &mut Vec<Value>,
        refs: &mut Vec<Value>,
        uses: &HashMap<(String, i64), Vec<RustUse>>,
        owner: Option<&str>,
    ) -> Value {
        let mut owners: BTreeMap<String, i64> = BTreeMap::new();
        for row in defs.iter() {
            let owner = string_field(row, "owner");
            if !owner.is_empty() {
                owners.entry(owner).or_insert(0);
            }
        }
        let mut name_only = 0i64;
        for row in refs.iter_mut() {
            let found = uses.get(&row_key(row));
            let pick = found.and_then(|list| {
                list.iter()
                    .find(|one| is_field_use(one.kind) && one.owner.is_some())
                    .or_else(|| list.iter().find(|one| is_field_use(one.kind)))
            });
            let kind = string_field(row, "kind");
            let Some(found) = pick else {
                if is_field_use(&kind) {
                    set_field(row, "name_only", Value::Bool(true));
                    name_only += 1;
                }
                continue;
            };
            if !is_field_use(&kind) {
                set_field(row, "kind", Value::string(found.kind));
            }
            match &found.owner {
                Some(type_name) => {
                    set_field(row, "owner", Value::string(type_name));
                    *owners.entry(type_name.clone()).or_insert(0) += 1;
                }
                None => {
                    set_field(row, "name_only", Value::Bool(true));
                    name_only += 1;
                }
            }
            if found.write {
                set_field(row, "write", Value::Bool(true));
            }
        }
        if let Some(owner) = owner {
            // One type's rows: its field uses, and code lines that are not
            // field uses at all (a local of the same name) are dropped.
            refs.retain(|row| string_field(row, "owner") == owner);
            owners.retain(|name, _| name == owner);
        }
        // Owned field rows first, grouped by owner; then the rest by place.
        refs.sort_by(|left, right| {
            let left_owner = string_field(left, "owner");
            let right_owner = string_field(right, "owner");
            left_owner
                .is_empty()
                .cmp(&right_owner.is_empty())
                .then(left_owner.cmp(&right_owner))
                .then(
                    string_field(left, "document_path").cmp(&string_field(right, "document_path")),
                )
                .then(int_field(left, "line_number").cmp(&int_field(right, "line_number")))
        });
        defs.sort_by(|left, right| {
            string_field(left, "document_path")
                .cmp(&string_field(right, "document_path"))
                .then(int_field(left, "line_number").cmp(&int_field(right, "line_number")))
        });
        let field_types: Vec<String> = owners.keys().cloned().collect();
        let mut block = vec![
            (
                "owners".to_string(),
                Value::Object(
                    owners
                        .into_iter()
                        .map(|(name, count)| (name, Value::Int(count)))
                        .collect(),
                ),
            ),
            ("name_only".to_string(), Value::Int(name_only)),
        ];
        if name_only > 0 {
            block.push((
                "note".to_string(),
                Value::string(format!(
                    "name_only rows read the field on a receiver whose type the line does not write (Stele does not infer types); each may belong to {}.",
                    match field_types.len() {
                        0 => "any type with this field".to_string(),
                        count if count > NOTE_TYPES => format!(
                            "any of {} and {} more (see owners)",
                            field_types[..NOTE_TYPES].join(", "),
                            count - NOTE_TYPES
                        ),
                        _ => format!("any of {}", field_types.join(", ")),
                    }
                )),
            ));
        }
        Value::Object(block)
    }

    /// The lines of Rust code that name `name` as a word and that neither
    /// a definition nor a reference row already gives, as reference rows,
    /// and how every such line uses it (`symbols::rust_uses`). `kind` is
    /// `call` (`name(`), `method_call` (`.name(` or `::name(`), `macro`
    /// (`name!`), `field_access` (`.name`), `struct_field` (`Type { name }`),
    /// else `mention`. With `whole_files` (a field name) a file is read
    /// whole from its chunks, so `self.name` finds the `impl` it is in;
    /// otherwise only the chunks that hold the name are read.
    pub(super) fn rust_mentions(
        &self,
        name: &str,
        defs: &[Value],
        refs: &[Value],
        whole_files: bool,
    ) -> (Vec<Value>, HashMap<(String, i64), Vec<RustUse>>) {
        let mut found: HashMap<(String, i64), Vec<RustUse>> = HashMap::new();
        if name.is_empty() || !name.chars().all(|ch| ch.is_alphanumeric() || ch == '_') {
            return (Vec::new(), found);
        }
        let mut known: HashSet<(String, i64)> = defs.iter().chain(refs).map(row_key).collect();
        let mut by_path: BTreeMap<&str, Vec<&Chunk>> = BTreeMap::new();
        for chunk in &self.store.state.chunks {
            if language_of(&chunk.path) == "rust" {
                by_path.entry(chunk.path.as_str()).or_default().push(chunk);
            }
        }
        let mut out = Vec::new();
        for (path, mut chunks) in by_path {
            if !chunks.iter().any(|chunk| chunk.content.contains(name)) {
                continue;
            }
            chunks.sort_by_key(|chunk| chunk.start);
            let mut lines: Vec<(u32, &Chunk, RustUse)> = Vec::new();
            if !whole_files || chunks.iter().any(|chunk| chunk.line_map.is_empty()) {
                // Each chunk alone: the owner of a field use is not asked
                // for, or the store has no file lines (before 2.3).
                for chunk in chunks.iter().filter(|chunk| chunk.content.contains(name)) {
                    for one in crate::symbols::rust_uses(&chunk.content, name) {
                        let line = crate::chunk::lines::absolute(&chunk.line_map, one.line);
                        lines.push((line, chunk, one));
                    }
                }
            } else {
                let (text, owners) = file_text(&chunks);
                for one in crate::symbols::rust_uses(&text, name) {
                    let chunk = owners
                        .get(one.line as usize)
                        .copied()
                        .flatten()
                        .unwrap_or(chunks[0]);
                    lines.push((one.line, chunk, one));
                }
            }
            for (line, chunk, one) in lines {
                let key = (path.to_string(), line as i64);
                if known.insert(key.clone()) {
                    out.push(Value::object(vec![
                        ("symbol", Value::string(name)),
                        ("kind", Value::string(one.kind)),
                        ("chunk_id", Value::string(&chunk.id)),
                        ("document_path", Value::string(path)),
                        ("line_number", Value::Int(line as i64)),
                        (
                            "content_preview",
                            Value::string(chunk.content.chars().take(200).collect::<String>()),
                        ),
                        ("dynamic", Value::Bool(false)),
                    ]));
                }
                found.entry(key).or_default().push(one);
            }
        }
        (out, found)
    }

    /// 2.5 `named_in`: lines that name `name` as a word outside the code
    /// the symbol rows read: files of a language the symbol is not defined
    /// in (scripts, docs, configs) and Rust string literals. A snake_case
    /// name also matches its kebab-case form (`--all-languages`). Lines in
    /// `known` are left out. Names shorter than four characters and plain
    /// words (`tasks`, `Index`) are skipped unless `force`.
    pub(super) fn named_in(
        &self,
        name: &str,
        languages: &[&str],
        known: &HashSet<(String, i64)>,
        force: bool,
    ) -> Value {
        let length = name.chars().count();
        if name.is_empty() || !name.chars().all(|ch| ch.is_alphanumeric() || ch == '_') {
            return Value::object(vec![("skipped", Value::string("not an identifier"))]);
        }
        if !force && length < NAMED_MIN_CHARS {
            return Value::object(vec![(
                "skipped",
                Value::string(format!(
                    "shorter than {NAMED_MIN_CHARS} characters; named_in=true scans anyway"
                )),
            )]);
        }
        if !force && plain_word(name) {
            return Value::object(vec![(
                "skipped",
                Value::string(
                    "one plain word, too common in prose and strings to mean this symbol; named_in=true scans anyway",
                ),
            )]);
        }
        let mut needles: Vec<Vec<char>> = vec![name.chars().collect()];
        let kebab = name.replace('_', "-");
        if kebab != name && !kebab.starts_with('-') && !kebab.ends_with('-') {
            needles.push(kebab.chars().collect());
        }
        // Same-language code is the reference rows' job; its strings are
        // not. With no definition, Rust code counts as the symbol's own.
        let same: Vec<&str> = if languages.is_empty() {
            vec!["rust"]
        } else {
            languages.to_vec()
        };
        let mut files: BTreeMap<String, BTreeSet<i64>> = BTreeMap::new();
        let texts: Vec<String> = needles
            .iter()
            .map(|needle| needle.iter().collect())
            .collect();
        for chunk in &self.store.state.chunks {
            if !texts
                .iter()
                .any(|text| chunk.content.contains(text.as_str()))
            {
                continue;
            }
            let language = language_of(&chunk.path);
            let text: Vec<char> = if same.contains(&language) {
                if language != "rust" {
                    continue;
                }
                crate::symbols::rust_strings(&chunk.content)
            } else {
                chunk.content.chars().collect()
            };
            for line in word_lines(&text, &needles) {
                let file_line = crate::chunk::lines::absolute(&chunk.line_map, line) as i64;
                if known.contains(&(chunk.path.clone(), file_line)) {
                    continue;
                }
                files
                    .entry(chunk.path.clone())
                    .or_default()
                    .insert(file_line);
            }
        }
        let lines_total: usize = files.values().map(BTreeSet::len).sum();
        let files_total = files.len();
        let mut rows: Vec<(String, BTreeSet<i64>)> = files.into_iter().collect();
        rows.sort_by(|left, right| right.1.len().cmp(&left.1.len()).then(left.0.cmp(&right.0)));
        let listed: Vec<Value> = rows
            .into_iter()
            .take(NAMED_FILES)
            .map(|(path, lines)| {
                Value::object(vec![
                    ("path", Value::string(path)),
                    ("count", Value::Int(lines.len() as i64)),
                    (
                        "lines",
                        Value::Array(
                            lines
                                .into_iter()
                                .take(NAMED_LINES)
                                .map(Value::Int)
                                .collect(),
                        ),
                    ),
                ])
            })
            .collect();
        let mut out = vec![
            ("files_total", Value::Int(files_total as i64)),
            ("lines_total", Value::Int(lines_total as i64)),
            ("files", Value::Array(listed)),
        ];
        if files_total > NAMED_FILES {
            out.push((
                "omitted_files",
                Value::Int((files_total - NAMED_FILES) as i64),
            ));
        }
        Value::object(out)
    }

    /// `named_in` for `impact_radius` (2.5): the files that name a symbol
    /// outside its own language's code, which a rename breaks and the
    /// symbol graph cannot see.
    pub(super) fn named_in_for(&self, symbol: &str, mode: Option<bool>) -> Option<Value> {
        if mode == Some(false) || symbol.is_empty() {
            return None;
        }
        let defs = self.reference_rows(symbol, true);
        let languages: Vec<&str> = defs
            .iter()
            .map(|row| language_of(&string_field(row, "document_path")))
            .collect();
        let known: HashSet<(String, i64)> = defs.iter().map(row_key).collect();
        Some(self.named_in(symbol, &languages, &known, mode == Some(true)))
    }
}

/// Reference rows by file, then line.
fn sort_rows(rows: &mut [Value]) {
    rows.sort_by(|left, right| {
        string_field(left, "document_path")
            .cmp(&string_field(right, "document_path"))
            .then(int_field(left, "line_number").cmp(&int_field(right, "line_number")))
    });
}

/// A file's text rebuilt from its chunks (sorted by start), with file
/// lines in place, and the chunk that holds each 1-based line.
pub(super) fn file_text<'a>(chunks: &[&'a Chunk]) -> (String, Vec<Option<&'a Chunk>>) {
    let mut lines: Vec<Option<(&'a str, &'a Chunk)>> = vec![None];
    for chunk in chunks {
        for (index, text) in crate::chunk::lines::content_lines(&chunk.content)
            .into_iter()
            .enumerate()
        {
            // A blank chunk line can map onto the file line after it (merged
            // chunks are joined with blank lines); only text is placed.
            if text.trim().is_empty() {
                continue;
            }
            let line = crate::chunk::lines::absolute(&chunk.line_map, index as u32 + 1) as usize;
            if line >= lines.len() {
                lines.resize(line + 1, None);
            }
            if lines[line].is_none() {
                lines[line] = Some((text, chunk));
            }
        }
    }
    let text = lines[1..]
        .iter()
        .map(|slot| slot.map(|(text, _)| text).unwrap_or(""))
        .collect::<Vec<_>>()
        .join("\n");
    let owners = lines
        .iter()
        .map(|slot| slot.map(|(_, chunk)| chunk))
        .collect();
    (text, owners)
}

/// One word with no `_`, digit or inner capital: `tasks`, `Index`, `HTTP`.
fn plain_word(name: &str) -> bool {
    let chars: Vec<char> = name.chars().collect();
    if !chars.iter().all(|ch| ch.is_alphabetic()) {
        return false;
    }
    let hump = chars
        .windows(2)
        .any(|pair| pair[0].is_lowercase() && pair[1].is_uppercase())
        || chars.windows(3).any(|three| {
            three[0].is_uppercase() && three[1].is_uppercase() && three[2].is_lowercase()
        });
    !hump
}

/// The 1-based lines of `text` where one of `needles` stands as a word: no
/// letter, digit or `_` on either side, and no `-` and word after it.
fn word_lines(text: &[char], needles: &[Vec<char>]) -> BTreeSet<u32> {
    let word = |ch: char| ch.is_alphanumeric() || ch == '_';
    let mut out = BTreeSet::new();
    let mut line = 1u32;
    for at in 0..text.len() {
        if text[at] == '\n' {
            line += 1;
            continue;
        }
        if at > 0 && word(text[at - 1]) {
            continue;
        }
        for needle in needles {
            let end = at + needle.len();
            if end > text.len() || text[at..end] != needle[..] {
                continue;
            }
            let after = text.get(end).copied();
            let joined = after == Some('-') && text.get(end + 1).is_some_and(|ch| word(*ch));
            if !after.is_some_and(word) && !joined {
                out.insert(line);
            }
        }
    }
    out
}
