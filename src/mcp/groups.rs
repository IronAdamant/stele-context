//! MCP tool groups (the suite-wide convention). Every tool belongs to one
//! group and `core` is always on. The operator sets `STELE_TOOLS` and
//! `STELE_DISABLE`; the agent sets the `stele:` lines of the `agent-tools`
//! block in the project's AGENTS.md. Per key the env var wins.
//! `STELE_MCP_MODE` still picks the catalogue (lite, standard, full) and the
//! groups then hide parts of it. With nothing set tools/list is unchanged.

use std::path::{Path, PathBuf};
use std::sync::RwLock;
use std::time::SystemTime;

/// Group of each MCP tool name, across the full catalogue.
const GROUPS: &[(&str, &[&str])] = &[
    (
        "core",
        &[
            "doctor",
            "map",
            "get_context",
            "detect_modality",
            "get_supported_formats",
            "environment_check",
            "clean_bytecache",
            "batch",
            "stats",
            "project_brief",
        ],
    ),
    (
        "index",
        &[
            "index",
            "remove",
            "detect_changes",
            "history",
            "prune_history",
            "get_chunk_history",
            "stale_chunks",
        ],
    ),
    (
        "search",
        &[
            "search",
            "search_text",
            "agent_grep",
            "query",
            "get_search_history",
            "get_session_read_files",
        ],
    ),
    (
        "symbols",
        &[
            "find_definition",
            "find_references",
            "impact_radius",
            "coupling",
            "rebuild_symbols",
            "register_dynamic_symbols",
            "remove_dynamic_symbols",
            "get_dynamic_symbols",
        ],
    ),
    (
        "embeddings",
        &[
            "llm_embed",
            "bulk_store_summaries",
            "bulk_store_embeddings",
            "enrichment_plan",
            "store_semantic_summary",
            "store_embedding",
        ],
    ),
    (
        "notes",
        &[
            "annotations",
            "annotate",
            "get_annotations",
            "delete_annotation",
            "update_annotation",
            "search_annotations",
            "bulk_annotate",
            "bulk_store_chunk_agent_notes",
            "store_chunk_agent_notes",
        ],
    ),
    (
        "locks",
        &[
            "document_lock",
            "acquire_document_lock",
            "refresh_document_lock",
            "release_document_lock",
            "get_document_lock_status",
            "release_agent_locks",
            "get_conflicts",
            "reap_expired_locks",
            "list_agents",
        ],
    ),
    ("messages", &["get_notifications"]),
    (
        "kv",
        &[
            "save_kv_state",
            "get_relevant_kv",
            "rollback",
            "prune_chunks",
            "list_sessions",
        ],
    ),
];

/// The name a project's AGENTS.md `agent-tools` block uses for Stele.
const TOOL: &str = "stele";

pub(crate) fn group_of(tool: &str) -> Option<&'static str> {
    GROUPS
        .iter()
        .find(|(_, tools)| tools.contains(&tool))
        .map(|(group, _)| *group)
}

fn group_names() -> String {
    GROUPS
        .iter()
        .map(|(group, _)| *group)
        .collect::<Vec<_>>()
        .join(", ")
}

/// Why a tool is hidden.
#[derive(Clone, Debug, PartialEq)]
pub(crate) enum Hidden {
    /// Not named in the allowlist (`STELE_TOOLS` or a `stele: tools` line).
    NotListed,
    /// Named in the denylist (by group or by tool name).
    Disabled,
}

/// Where a list came from: the operator's env var or the agent's AGENTS.md.
#[derive(Clone, Copy, Debug, PartialEq)]
enum Source {
    Env,
    File,
}

impl Source {
    fn name(self, key: &str) -> String {
        match (self, key) {
            (Source::Env, "tools") => "STELE_TOOLS".to_string(),
            (Source::Env, _) => "STELE_DISABLE".to_string(),
            (Source::File, _) => format!("the \"{TOOL}: {key}\" line in AGENTS.md"),
        }
    }
}

#[derive(Clone, Debug, PartialEq)]
pub(crate) struct Selection {
    /// Allowlist entries (lowercase), `None` when unset.
    allow: Option<Vec<String>>,
    allow_from: Source,
    /// Denylist entries (lowercase).
    deny: Vec<String>,
    deny_from: Source,
}

const NOTHING: Selection = Selection {
    allow: None,
    allow_from: Source::Env,
    deny: Vec::new(),
    deny_from: Source::Env,
};

impl Selection {
    /// Parse the two variables alone. `Err` is the one stderr line the
    /// server prints before it exits with code 2.
    #[cfg(test)]
    pub(crate) fn parse(tools: Option<&str>, disable: Option<&str>) -> Result<Selection, String> {
        let (allow, deny) = env_lists(tools, disable)?;
        Ok(Selection {
            allow,
            deny: deny.unwrap_or_default(),
            ..NOTHING
        })
    }

    /// Nothing is hidden: the listing is exactly the mode's.
    pub(crate) fn is_default(&self) -> bool {
        self.allow.is_none() && self.deny.is_empty()
    }

    /// `None` when the tool is exposed.
    pub(crate) fn hidden(&self, tool: &str) -> Option<Hidden> {
        let group = group_of(tool).unwrap_or("core");
        if group == "core" {
            return None;
        }
        if let Some(allow) = &self.allow {
            let all = allow.iter().any(|item| item == "all");
            if !all && !allow.iter().any(|item| item == group || item == tool) {
                return Some(Hidden::NotListed);
            }
        }
        if self.deny.iter().any(|item| item == group || item == tool) {
            return Some(Hidden::Disabled);
        }
        None
    }

    /// The one-line error for a call to a hidden tool.
    pub(crate) fn refusal(&self, tool: &str, why: &Hidden) -> String {
        let group = group_of(tool).unwrap_or("core");
        match why {
            Hidden::Disabled => {
                let named = if self.deny.iter().any(|item| item == tool) {
                    tool
                } else {
                    group
                };
                format!(
                    "tool \"{tool}\" is disabled (group \"{group}\") by {}; remove \"{named}\" from it to enable",
                    self.deny_from.name("disable")
                )
            }
            Hidden::NotListed => format!(
                "tool \"{tool}\" is disabled (group \"{group}\") by {}; add \"{group}\" to it to enable",
                self.allow_from.name("tools")
            ),
        }
    }

    /// The exposed tools of `catalogue`, in order.
    fn visible<'a>(&self, catalogue: &[&'a str]) -> Vec<&'a str> {
        catalogue
            .iter()
            .copied()
            .filter(|tool| self.hidden(tool).is_none())
            .collect()
    }

    /// One line: the groups of this mode's catalogue that are on and off,
    /// what set them, and how an agent changes them.
    pub(crate) fn summary(&self, catalogue: &[&str]) -> String {
        let mut on: Vec<&str> = Vec::new();
        let mut off: Vec<&str> = Vec::new();
        for (group, tools) in GROUPS {
            let present: Vec<&&str> = tools
                .iter()
                .filter(|tool| catalogue.contains(tool))
                .collect();
            if present.is_empty() {
                continue;
            }
            if present.iter().any(|tool| self.hidden(tool).is_none()) {
                on.push(group);
            } else {
                off.push(group);
            }
        }
        let mut set = Vec::new();
        if let Some(allow) = &self.allow {
            set.push(match self.allow_from {
                Source::Env => format!("STELE_TOOLS={}", allow.join(",")),
                Source::File => format!("AGENTS.md {TOOL}: tools {}", allow.join(", ")),
            });
        }
        if !self.deny.is_empty() {
            set.push(match self.deny_from {
                Source::Env => format!("STELE_DISABLE={}", self.deny.join(",")),
                Source::File => format!("AGENTS.md {TOOL}: disable {}", self.deny.join(", ")),
            });
        }
        let off = if off.is_empty() {
            "none".to_string()
        } else {
            off.join(", ")
        };
        let set = if set.is_empty() {
            String::new()
        } else {
            format!(" (set by {})", set.join("; "))
        };
        format!(
            "Tool groups on: {}; off: {off}{set}. Change them for this project with a \"{TOOL}: disable <groups>\" or \"{TOOL}: tools <groups>\" line in an agent-tools block in AGENTS.md.",
            on.join(", ")
        )
    }
}

/// The env lists, validated. Blank values count as unset.
fn env_lists(
    tools: Option<&str>,
    disable: Option<&str>,
) -> Result<(Option<Vec<String>>, Option<Vec<String>>), String> {
    let tools = tools.filter(|text| !text.trim().is_empty());
    let disable = disable.filter(|text| !text.trim().is_empty());
    let allow = match tools {
        Some(text) => Some(
            checked(&split(text, false), true, false)
                .map_err(|err| format!("STELE_TOOLS: {err}"))?,
        ),
        None => None,
    };
    let deny = match disable {
        Some(text) => Some(checked(&split(text, false), false, false).map_err(|err| {
            if err.contains("always on") {
                let item = err.split('"').nth(1).unwrap_or("core");
                format!("STELE_DISABLE: {err}; remove \"{item}\" from it")
            } else {
                format!("STELE_DISABLE: {err}")
            }
        })?),
        None => None,
    };
    Ok((allow, deny))
}

/// Lowercase entries, whitespace and case ignored. Env lists are comma
/// separated; AGENTS.md lists take commas or spaces.
fn split(text: &str, spaces: bool) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    for raw in text.split(|ch: char| ch == ',' || (spaces && ch.is_whitespace())) {
        let item: String = raw
            .chars()
            .filter(|ch| !ch.is_whitespace())
            .collect::<String>()
            .to_lowercase();
        if !item.is_empty() && !out.contains(&item) {
            out.push(item);
        }
    }
    out
}

/// Every entry must be a group or tool name (`all` only in an allowlist),
/// and a denylist may not name `core` or a core tool. `Err` names the
/// first problem and the valid names.
fn checked(items: &[String], allow_all: bool, short: bool) -> Result<Vec<String>, String> {
    for item in items {
        let known = GROUPS
            .iter()
            .any(|(group, tools)| *group == item.as_str() || tools.contains(&item.as_str()))
            || (allow_all && item == "all");
        if !known {
            let kind = if item.contains('_') { "tool" } else { "group" };
            let mut line = format!("unknown {kind} \"{item}\"");
            if short {
                line.push_str(&format!(" for {TOOL}; groups: {}", group_names()));
            } else {
                let tools: Vec<&str> = GROUPS
                    .iter()
                    .flat_map(|(_, tools)| tools.iter().copied())
                    .collect();
                line.push_str(&format!(
                    "; groups: {}; tools: {}",
                    group_names(),
                    tools.join(", ")
                ));
            }
            return Err(line);
        }
        if !allow_all {
            if item == "core" {
                return Err("group \"core\" is always on and cannot be disabled".to_string());
            }
            if group_of(item) == Some("core") {
                return Err(format!(
                    "tool \"{item}\" is in group \"core\", which is always on"
                ));
            }
        }
    }
    Ok(items.to_vec())
}

/// The `stele` lines of the first fenced block whose info string is
/// `agent-tools`, as (key, list, line text). Lines for other tools are
/// skipped; a line that names Stele but has no known key is a warning.
fn agent_tools_lines(text: &str) -> (Vec<(String, String)>, Vec<String>) {
    let mut lines = Vec::new();
    let mut warnings = Vec::new();
    let mut fence: Option<(char, usize)> = None;
    let mut inside = false;
    for line in text.lines() {
        let body = line.trim_start_matches(' ');
        let opener = if line.len() - body.len() <= 3 {
            fence_of(body)
        } else {
            None
        };
        match fence {
            None => {
                if let Some((ch, len, info)) = opener {
                    fence = Some((ch, len));
                    inside = info.split_whitespace().next() == Some("agent-tools");
                }
            }
            Some((ch, len)) => {
                if let Some((close, count, info)) = opener {
                    if close == ch && count >= len && info.is_empty() {
                        if inside {
                            return (lines, warnings);
                        }
                        fence = None;
                        continue;
                    }
                }
                if !inside {
                    continue;
                }
                let content = line.split('#').next().unwrap_or("").trim();
                let Some((tool, rest)) = content.split_once(':') else {
                    continue;
                };
                let tool = tool.trim();
                if tool != TOOL && tool != "stele-context" {
                    continue;
                }
                let rest = rest.trim();
                let (key, list) = rest.split_once(char::is_whitespace).unwrap_or((rest, ""));
                let key = key.to_lowercase();
                if key == "tools" || key == "disable" {
                    lines.push((key, list.to_string()));
                } else {
                    warnings.push(format!(
                        "AGENTS.md agent-tools: unknown key \"{key}\" for {TOOL}; use \"{TOOL}: tools <list>\" or \"{TOOL}: disable <list>\""
                    ));
                }
            }
        }
    }
    (lines, warnings)
}

/// A fence line: its character, length and info string.
fn fence_of(line: &str) -> Option<(char, usize, &str)> {
    let ch = line.chars().next()?;
    if ch != '`' && ch != '~' {
        return None;
    }
    let count = line.chars().take_while(|c| *c == ch).count();
    if count < 3 {
        return None;
    }
    let info = line[count..].trim();
    if ch == '`' && info.contains('`') {
        return None;
    }
    Some((ch, count, info))
}

/// What the project's AGENTS.md asks for: the lists of its valid lines and
/// one warning per ignored line.
#[derive(Debug, Default, PartialEq)]
struct FileLists {
    tools: Option<Vec<String>>,
    disable: Option<Vec<String>>,
    warnings: Vec<String>,
}

fn file_lists(text: &str) -> FileLists {
    let (lines, mut warnings) = agent_tools_lines(text);
    let mut out = FileLists::default();
    for (key, list) in lines {
        let items = split(&list, true);
        if items.is_empty() {
            continue;
        }
        let allow_all = key == "tools";
        match checked(&items, allow_all, true) {
            Ok(items) => {
                let slot = if allow_all {
                    &mut out.tools
                } else {
                    &mut out.disable
                };
                let merged = slot.get_or_insert_with(Vec::new);
                for item in items {
                    if !merged.contains(&item) {
                        merged.push(item);
                    }
                }
            }
            Err(err) => {
                let err = if err.starts_with("unknown") {
                    err
                } else {
                    format!("{err} for {TOOL}")
                };
                warnings.push(format!(
                    "AGENTS.md agent-tools: {err}; line \"{TOOL}: {key} {}\" ignored",
                    list.trim()
                ));
            }
        }
    }
    out.warnings = warnings;
    out
}

/// The live MCP surface: env lists (fixed at startup), the project's
/// AGENTS.md (re-read when its mtime or size changes) and the result.
pub(crate) struct Surface {
    env_tools: Option<Vec<String>>,
    env_disable: Option<Vec<String>>,
    file: PathBuf,
    stamp: Option<(Option<SystemTime>, u64)>,
    selection: Selection,
    warnings: Vec<String>,
    /// The server's surface: its selection is what tools/call, batch and
    /// doctor read. Off in unit tests, which share the process.
    published: bool,
}

impl Surface {
    /// Read the env vars (an error is the exit-2 stderr line) and the
    /// AGENTS.md of `project`.
    pub(crate) fn open(project: &Path) -> Result<Surface, String> {
        let tools = std::env::var("STELE_TOOLS").ok();
        let disable = std::env::var("STELE_DISABLE").ok();
        let mut surface = Surface::with(project, tools.as_deref(), disable.as_deref())?;
        surface.published = true;
        surface.publish();
        Ok(surface)
    }

    pub(crate) fn with(
        project: &Path,
        tools: Option<&str>,
        disable: Option<&str>,
    ) -> Result<Surface, String> {
        let (env_tools, env_disable) = env_lists(tools, disable)?;
        let mut surface = Surface {
            env_tools,
            env_disable,
            file: project.join("AGENTS.md"),
            stamp: None,
            selection: NOTHING,
            warnings: Vec::new(),
            published: false,
        };
        surface.reload();
        Ok(surface)
    }

    /// Stat AGENTS.md and reload it when it changed. True when the set of
    /// exposed tools in `catalogue` changed.
    pub(crate) fn refresh(&mut self, catalogue: &[&str]) -> bool {
        if stamp_of(&self.file) == self.stamp {
            return false;
        }
        let before = self.selection.visible(catalogue);
        self.reload();
        self.selection.visible(catalogue) != before
    }

    fn reload(&mut self) {
        self.stamp = stamp_of(&self.file);
        let lists = match self.stamp {
            Some(_) => match std::fs::read(&self.file) {
                Ok(bytes) => file_lists(&String::from_utf8_lossy(&bytes)),
                Err(_) => FileLists::default(),
            },
            None => FileLists::default(),
        };
        // Per key: the operator's env var beats the agent's line.
        let (allow, allow_from) = match (&self.env_tools, lists.tools) {
            (Some(env), _) => (Some(env.clone()), Source::Env),
            (None, file) => (file, Source::File),
        };
        let (deny, deny_from) = match (&self.env_disable, lists.disable) {
            (Some(env), _) => (env.clone(), Source::Env),
            (None, file) => (file.unwrap_or_default(), Source::File),
        };
        self.selection = Selection {
            allow,
            allow_from,
            deny,
            deny_from,
        };
        self.warnings = lists.warnings;
        self.publish();
    }

    fn publish(&self) {
        if !self.published {
            return;
        }
        if let Ok(mut active) = ACTIVE.write() {
            *active = (self.selection.clone(), self.warnings.clone());
        }
    }

    pub(crate) fn selection(&self) -> &Selection {
        &self.selection
    }

    /// The initialize instructions: any AGENTS.md warnings, then the
    /// groups line.
    pub(crate) fn instructions(&self, catalogue: &[&str]) -> String {
        let mut lines = self.warnings.clone();
        lines.push(self.selection.summary(catalogue));
        lines.join("\n")
    }
}

fn stamp_of(path: &Path) -> Option<(Option<SystemTime>, u64)> {
    let meta = std::fs::metadata(path).ok()?;
    Some((meta.modified().ok(), meta.len()))
}

/// The server's current selection and AGENTS.md warnings, for tools/call,
/// batch and doctor. Outside the MCP server nothing is hidden.
static ACTIVE: RwLock<(Selection, Vec<String>)> = RwLock::new((NOTHING, Vec::new()));

pub(crate) fn active() -> Selection {
    ACTIVE
        .read()
        .map(|active| active.0.clone())
        .unwrap_or(NOTHING)
}

/// Doctor's extra fields: the groups line when anything is hidden or
/// AGENTS.md has a warning; nothing otherwise.
pub(crate) fn doctor_fields(catalogue: &[&str]) -> Vec<(String, String)> {
    let Ok(active) = ACTIVE.read() else {
        return Vec::new();
    };
    let (selection, warnings) = &*active;
    let mut out = Vec::new();
    if !selection.is_default() || !warnings.is_empty() {
        out.push(("tool_groups".to_string(), selection.summary(catalogue)));
    }
    if !warnings.is_empty() {
        out.push(("tool_groups_warning".to_string(), warnings.join(" | ")));
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_catalogue_tool_has_one_group() {
        for name in super::super::all_tool_names() {
            let owners = GROUPS
                .iter()
                .filter(|(_, tools)| tools.contains(&name))
                .count();
            assert_eq!(owners, 1, "{name}");
        }
        let total: usize = GROUPS.iter().map(|(_, tools)| tools.len()).sum();
        assert_eq!(total, super::super::all_tool_names().len());
    }

    #[test]
    fn allow_and_disable_by_group_and_tool() {
        let only = Selection::parse(Some(" Search , find_definition"), None).unwrap();
        assert!(only.hidden("query").is_none());
        assert!(only.hidden("find_definition").is_none());
        assert_eq!(only.hidden("find_references"), Some(Hidden::NotListed));
        assert!(only.hidden("doctor").is_none(), "core stays on");
        let off = Selection::parse(None, Some("LOCKS,notes, query")).unwrap();
        assert_eq!(off.hidden("document_lock"), Some(Hidden::Disabled));
        assert_eq!(off.hidden("annotations"), Some(Hidden::Disabled));
        assert_eq!(off.hidden("query"), Some(Hidden::Disabled));
        assert!(off.hidden("search").is_none());
        let both = Selection::parse(Some("all"), Some("kv")).unwrap();
        assert!(both.hidden("index").is_none());
        assert_eq!(both.hidden("save_kv_state"), Some(Hidden::Disabled));
        assert!(Selection::parse(None, None).unwrap().is_default());
    }

    #[test]
    fn core_cannot_be_disabled() {
        let only = Selection::parse(Some("locks"), None).unwrap();
        assert!(only.hidden("doctor").is_none());
        assert!(only.hidden("get_context").is_none());
        assert_eq!(only.hidden("search"), Some(Hidden::NotListed));
        assert_eq!(
            Selection::parse(None, Some("core")).unwrap_err(),
            "STELE_DISABLE: group \"core\" is always on and cannot be disabled; remove \"core\" from it"
        );
        assert_eq!(
            Selection::parse(None, Some("locks, Doctor")).unwrap_err(),
            "STELE_DISABLE: tool \"doctor\" is in group \"core\", which is always on; remove \"doctor\" from it"
        );
    }

    #[test]
    fn blank_values_count_as_unset() {
        assert!(Selection::parse(Some(""), Some("   "))
            .unwrap()
            .is_default());
        let summary = Selection::parse(Some("search"), Some("query"))
            .unwrap()
            .summary(&["doctor", "query", "search", "index", "document_lock"]);
        assert!(
            summary.starts_with(
                "Tool groups on: core, search; off: index, locks (set by STELE_TOOLS=search; STELE_DISABLE=query). Change them"
            ),
            "{summary}"
        );
    }

    #[test]
    fn refusal_and_unknown_messages() {
        let off = Selection::parse(None, Some("locks")).unwrap();
        assert_eq!(
            off.refusal("document_lock", &Hidden::Disabled),
            "tool \"document_lock\" is disabled (group \"locks\") by STELE_DISABLE; remove \"locks\" from it to enable"
        );
        let err = Selection::parse(None, Some("lokcs")).unwrap_err();
        assert!(err.starts_with(
            "STELE_DISABLE: unknown group \"lokcs\"; groups: core, index, search, symbols, embeddings, notes, locks, messages, kv; tools: doctor, map,"
        ), "{err}");
        let err = Selection::parse(Some("serch_text"), None).unwrap_err();
        assert!(
            err.starts_with("STELE_TOOLS: unknown tool \"serch_text\""),
            "{err}"
        );
        assert!(
            Selection::parse(None, Some("all")).is_err(),
            "all only in STELE_TOOLS"
        );
    }

    #[test]
    fn agents_md_block_is_read_like_markdown() {
        let text = "# Notes\n\n````markdown\n```agent-tools\nstele: disable kv\n```\n````\n\n```agent-tools\n# one agent works here\ntrammel: disable locks, notes\nstele: disable locks notes  # comment\nstele: tools all\nstele: disable lokcs\nstele: disabel kv\nstele: disable doctor\n```\n\n```agent-tools\nstele: disable search\n```\n";
        let lists = file_lists(text);
        assert_eq!(lists.tools, Some(vec!["all".to_string()]));
        assert_eq!(
            lists.disable,
            Some(vec!["locks".to_string(), "notes".to_string()])
        );
        assert_eq!(
            lists.warnings,
            vec![
                "AGENTS.md agent-tools: unknown key \"disabel\" for stele; use \"stele: tools <list>\" or \"stele: disable <list>\"".to_string(),
                "AGENTS.md agent-tools: unknown group \"lokcs\" for stele; groups: core, index, search, symbols, embeddings, notes, locks, messages, kv; line \"stele: disable lokcs\" ignored".to_string(),
                "AGENTS.md agent-tools: tool \"doctor\" is in group \"core\", which is always on for stele; line \"stele: disable doctor\" ignored".to_string(),
            ]
        );
        assert_eq!(file_lists("no block here\n"), FileLists::default());
        assert_eq!(
            file_lists("```agent-tools\nchisel: disable locks\n```\n"),
            FileLists::default()
        );
    }

    #[test]
    fn own_docs_examples_switch_nothing() {
        // The examples sit inside a ````markdown fence: text, not config.
        for text in [
            include_str!("../../AGENTS.md"),
            include_str!("../../README.md"),
        ] {
            assert!(text.contains("stele: disable locks, notes"));
            assert_eq!(file_lists(text), FileLists::default());
        }
    }

    #[test]
    fn env_beats_agents_md_per_key() {
        let dir = std::env::temp_dir().join(format!("stele-groups-unit-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(
            dir.join("AGENTS.md"),
            "```agent-tools\nstele: tools search, locks\nstele: disable locks\n```\n",
        )
        .unwrap();
        let surface = Surface::with(&dir, None, Some("kv")).unwrap();
        let selection = surface.selection();
        assert!(
            selection.hidden("document_lock").is_none(),
            "env replaced the file's disable line"
        );
        assert_eq!(
            selection.hidden("index"),
            Some(Hidden::NotListed),
            "the file's tools line still applies"
        );
        assert_eq!(
            selection.refusal("index", &Hidden::NotListed),
            "tool \"index\" is disabled (group \"index\") by the \"stele: tools\" line in AGENTS.md; add \"index\" to it to enable"
        );
        let surface = Surface::with(&dir, None, None).unwrap();
        assert_eq!(
            surface.selection().refusal("document_lock", &Hidden::Disabled),
            "tool \"document_lock\" is disabled (group \"locks\") by the \"stele: disable\" line in AGENTS.md; remove \"locks\" from it to enable"
        );
        let _ = std::fs::remove_dir_all(&dir);
    }
}
