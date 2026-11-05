//! Document locks, the conflict log, and the cross-worktree coordination file.
//!
//! Python 1.5.2 stores the same facts in `coordination.db` (SQLite). This
//! process keeps them in `<git-common-dir>/stele-context/coordination.json`
//! so two worktrees share holders and the conflict log without a database
//! crate. The in-snapshot lock remains the copy for a repo with no `.git`.

use crate::json::{self, Value};
use crate::store::{Conflict, State};
use std::fs;
use std::path::{Path, PathBuf};

pub const TTL: i64 = 300;

pub fn apply(
    state: &mut State,
    cwd: &Path,
    action: &str,
    path: &str,
    agent: &str,
    now: i64,
    force: bool,
) -> Result<Value, String> {
    let stored = crate::walk::native_sep(path);
    let path = stored.as_str();
    let mut shared = coordination_dir(cwd).and_then(|dir| Shared::load(&dir));
    let outcome = match action {
        "acquire" => acquire(state, shared.as_mut(), path, agent, now, force),
        "refresh" => refresh(state, shared.as_mut(), path, agent, now),
        "release" => Ok(release(state, shared.as_mut(), path, agent, false, now)),
        "release_all" => Ok(release(state, shared.as_mut(), path, agent, true, now)),
        "status" => Ok(status(state, shared.as_ref(), path, now, cwd)),
        "conflicts" => Ok(conflicts(state, shared.as_ref(), path)),
        "reap" => Ok(reap(state, shared.as_mut(), now)),
        _ => Err(format!("unknown document_lock action '{action}'")),
    };
    if let Some(shared) = shared.as_mut() {
        shared.save();
    }
    outcome
}

fn acquire(
    state: &mut State,
    mut shared: Option<&mut Shared>,
    path: &str,
    agent: &str,
    now: i64,
    force: bool,
) -> Result<Value, String> {
    let existing = {
        let view = shared.as_deref();
        live_holder(state, view, path, now)
    };
    if let Some((holder, until, auto)) = existing {
        // An index-time lock is not a claim: it yields to an explicit
        // acquire, the indexing agent's own included, with no conflict.
        if holder != agent && !auto && !force {
            return Ok(refused(shared.as_deref(), &holder, until));
        }
        if holder != agent && !auto && force {
            record_conflict(state, &mut shared, path, &holder, agent, now);
        }
    }
    let coordinated = shared.is_some();
    write_lock(state, shared, path, agent, now + TTL);
    if coordinated {
        return Ok(Value::object(vec![("acquired", Value::Bool(true))]));
    }
    let version = state
        .documents
        .iter()
        .find(|doc| doc.path == path)
        .map(|doc| doc.doc_version as i64)
        .unwrap_or(0);
    Ok(Value::object(vec![
        ("acquired", Value::Bool(true)),
        ("doc_version", Value::Int(version)),
    ]))
}

fn refresh(
    state: &mut State,
    shared: Option<&mut Shared>,
    path: &str,
    agent: &str,
    now: i64,
) -> Result<Value, String> {
    let existing = {
        let view = shared.as_deref();
        live_holder(state, view, path, now)
    };
    match existing {
        Some((holder, _, _)) if holder == agent => {
            write_lock(state, shared, path, agent, now + TTL);
            Ok(Value::object(vec![
                ("refreshed", Value::Bool(true)),
                ("expires_at", Value::Float((now + TTL) as f64)),
            ]))
        }
        Some(_) => Ok(Value::object(vec![
            ("refreshed", Value::Bool(false)),
            ("reason", Value::string("not_owner")),
        ])),
        None => Ok(Value::object(vec![
            ("refreshed", Value::Bool(false)),
            ("reason", Value::string("not_locked")),
        ])),
    }
}

fn write_lock(state: &mut State, shared: Option<&mut Shared>, path: &str, agent: &str, until: i64) {
    state.locks.retain(|lock| lock.path != path);
    state.locks.push(crate::store::Lock {
        path: path.to_string(),
        agent: agent.to_string(),
        until,
        auto: false,
    });
    if let Some(shared) = shared {
        shared.locks.retain(|lock| lock.path != path);
        shared.locks.push(crate::store::Lock {
            path: path.to_string(),
            agent: agent.to_string(),
            until,
            auto: false,
        });
    }
}

fn record_conflict(
    state: &mut State,
    shared: &mut Option<&mut Shared>,
    path: &str,
    holder: &str,
    agent: &str,
    now: i64,
) {
    let stamp = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_secs_f64())
        .unwrap_or(now as f64);
    let row = Conflict {
        path: path.to_string(),
        holder: holder.to_string(),
        requester: agent.to_string(),
        at: (stamp * 1_000_000.0).round() as i64,
        kind: "lock_stolen".to_string(),
    };
    // 1.5.2 writes a force-steal to the shared conflict log. stats counts
    // document_conflicts only, which stays empty for that event.
    if let Some(shared) = shared.as_mut() {
        shared.conflicts.push(row);
    } else {
        state.conflicts.push(row);
    }
}

fn release(
    state: &mut State,
    shared: Option<&mut Shared>,
    path: &str,
    agent: &str,
    all: bool,
    now: i64,
) -> Value {
    if all {
        let documents: Vec<Value> = if let Some(shared) = shared.as_ref() {
            shared
                .locks
                .iter()
                .filter(|lock| lock.agent == agent)
                .map(|lock| Value::string(&lock.path))
                .collect()
        } else {
            state
                .locks
                .iter()
                .filter(|lock| lock.agent == agent)
                .map(|lock| Value::string(&lock.path))
                .collect()
        };
        let released_count = documents.len() as i64;
        state.locks.retain(|lock| lock.agent != agent);
        if let Some(shared) = shared {
            shared.locks.retain(|lock| lock.agent != agent);
        }
        return Value::object(vec![
            ("released_count", Value::Int(released_count)),
            ("documents", Value::Array(documents)),
        ]);
    }
    let holder = {
        let view = shared.as_deref();
        live_holder(state, view, path, now)
    };
    match holder {
        None => Value::object(vec![
            ("released", Value::Bool(false)),
            ("reason", Value::string("not_locked")),
        ]),
        Some((holder, _, auto)) if holder != agent && !auto => Value::object(vec![
            ("released", Value::Bool(false)),
            ("reason", Value::string("not_owner")),
        ]),
        Some(_) => {
            state.locks.retain(|lock| lock.path != path);
            if let Some(shared) = shared {
                shared.locks.retain(|lock| lock.path != path);
            }
            Value::object(vec![("released", Value::Bool(true))])
        }
    }
}

fn status(state: &State, shared: Option<&Shared>, path: &str, now: i64, _cwd: &Path) -> Value {
    match live_holder(state, shared, path, now) {
        None => Value::object(vec![("locked", Value::Bool(false))]),
        Some((holder, until, auto)) => {
            let worktree = shared
                .and_then(|shared| {
                    shared.agents.iter().find_map(|row| {
                        if text_field(row, "agent_id") == holder {
                            Some(Value::string(text_field(row, "worktree_root")))
                        } else {
                            None
                        }
                    })
                })
                .unwrap_or(Value::Null);
            let mut pairs = vec![
                ("locked", Value::Bool(true)),
                ("locked_by", Value::string(holder)),
                ("locked_at", Value::Float((until - TTL) as f64)),
                ("expires_at", Value::Float(until as f64)),
                ("worktree", worktree),
            ];
            if auto {
                // Tell the agent it can take the file: `acquire` succeeds.
                pairs.push(("auto", Value::Bool(true)));
            }
            Value::object(pairs)
        }
    }
}

fn conflicts(state: &State, shared: Option<&Shared>, path: &str) -> Value {
    let rows: Vec<Conflict> = if let Some(shared) = shared {
        shared.conflicts.clone()
    } else {
        state.conflicts.clone()
    };
    let rendered = rows
        .into_iter()
        .filter(|row| path.is_empty() || row.path == path)
        .enumerate()
        .map(|(index, row)| {
            let resolution = if row.kind == "lock_stolen" {
                "force_overwritten"
            } else {
                "rejected"
            };
            Value::object(vec![
                ("id", Value::Int(index as i64 + 1)),
                ("document_path", Value::string(row.path)),
                ("agent_a", Value::string(row.holder)),
                ("agent_b", Value::string(row.requester)),
                ("conflict_type", Value::string(row.kind)),
                ("expected_version", Value::Null),
                ("actual_version", Value::Null),
                ("resolution", Value::string(resolution)),
                (
                    "created_at",
                    Value::Float(if row.at > 1_000_000_000_000 {
                        row.at as f64 / 1_000_000.0
                    } else {
                        row.at as f64
                    }),
                ),
            ])
        })
        .collect();
    Value::object(vec![("conflicts", Value::Array(rendered))])
}

fn reap(state: &mut State, shared: Option<&mut Shared>, now: i64) -> Value {
    let reaped = if let Some(shared) = shared {
        let before = shared.locks.len();
        shared.locks.retain(|lock| lock.until > now);
        state.locks.retain(|lock| lock.until > now);
        before - shared.locks.len()
    } else {
        let before = state.locks.len();
        state.locks.retain(|lock| lock.until > now);
        before - state.locks.len()
    };
    Value::object(vec![
        ("reaped_count", Value::Int(reaped as i64)),
        ("documents", Value::Array(Vec::new())),
    ])
}

fn refused(shared: Option<&Shared>, holder: &str, until: i64) -> Value {
    let worktree = shared
        .and_then(|shared| {
            shared.agents.iter().find_map(|row| {
                if text_field(row, "agent_id") == holder {
                    let root = text_field(row, "worktree_root");
                    if root.is_empty() {
                        None
                    } else {
                        Some(Value::string(root))
                    }
                } else {
                    None
                }
            })
        })
        .unwrap_or(Value::Null);
    Value::object(vec![
        ("acquired", Value::Bool(false)),
        ("locked_by", Value::string(holder)),
        ("locked_at", Value::Float((until - TTL) as f64)),
        ("expires_at", Value::Float(until as f64)),
        ("worktree", worktree),
    ])
}

/// The live holder of `path`: agent, expiry, and whether it is an
/// index-time lock.
fn live_holder(
    state: &State,
    shared: Option<&Shared>,
    path: &str,
    now: i64,
) -> Option<(String, i64, bool)> {
    // The coordination file is the holder for every worktree. A snapshot
    // copy can still name the agent who lost a force-steal.
    if let Some(shared) = shared {
        return shared.live(path, now);
    }
    state
        .locks
        .iter()
        .find(|lock| lock.path == path && lock.until > now)
        .map(|lock| (lock.agent.clone(), lock.until, lock.auto))
}

#[cfg(test)]
mod tests {
    use super::apply;
    use crate::json;
    use crate::store::State;
    use std::fs;

    #[test]
    fn force_steal_writes_the_shared_conflict_log() {
        let root = std::env::temp_dir().join(format!("stele-lock-{}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(root.join(".git")).unwrap();
        let mut state = State::default();
        let first = apply(
            &mut state, &root, "acquire", "a.py", "agent-a", 1_000, false,
        )
        .unwrap();
        assert!(json::to_string(&first).contains("\"acquired\":true"));
        let blocked = apply(
            &mut state, &root, "acquire", "a.py", "agent-b", 1_010, false,
        )
        .unwrap();
        let blocked_text = json::to_string(&blocked);
        assert!(
            blocked_text.contains("\"acquired\":false"),
            "{blocked_text}"
        );
        assert!(blocked_text.contains("agent-a"), "{blocked_text}");
        let stolen = apply(&mut state, &root, "acquire", "a.py", "agent-b", 1_020, true).unwrap();
        assert!(json::to_string(&stolen).contains("\"acquired\":true"));
        let log = apply(&mut state, &root, "conflicts", "a.py", "", 1_020, false).unwrap();
        let log_text = json::to_string(&log);
        assert!(log_text.contains("agent-b"), "{log_text}");
        assert!(log_text.contains("lock_stolen"), "{log_text}");
        let shared = fs::read_to_string(root.join(".git/stele-context/coordination.json")).unwrap();
        assert!(shared.contains("agent-b"), "{shared}");
        let status = apply(&mut state, &root, "status", "a.py", "", 1_020, false).unwrap();
        assert!(json::to_string(&status).contains("agent-b"));
        let _ = fs::remove_dir_all(&root);
    }

    /// 2.2: an index-time lock never blocks an explicit acquire (the
    /// indexing agent's own or another's) and never replaces an explicit
    /// lock; an explicit lock still refuses other agents.
    #[test]
    fn index_locks_yield_and_never_replace_explicit_locks() {
        for git in [false, true] {
            let root =
                std::env::temp_dir().join(format!("stele-lock-auto-{}-{git}", std::process::id()));
            let _ = fs::remove_dir_all(&root);
            fs::create_dir_all(&root).unwrap();
            if git {
                fs::create_dir_all(root.join(".git")).unwrap();
            }
            let mut state = State::default();
            let paths = vec!["a.py".to_string(), "b.py".to_string()];
            // The server indexes both files for its own agent id.
            super::hold_auto(&mut state.locks, &paths, "stele-context-mcp-1", 1_000);
            super::hold_shared(&root, &paths, "stele-context-mcp-1", 1_000);
            let status = json::to_string(
                &apply(&mut state, &root, "status", "a.py", "", 1_001, false).unwrap(),
            );
            assert!(status.contains("\"auto\":true"), "{status}");
            // The agent behind that server claims a.py under its own id.
            let mine = json::to_string(
                &apply(
                    &mut state, &root, "acquire", "a.py", "agent-a", 1_002, false,
                )
                .unwrap(),
            );
            assert!(mine.contains("\"acquired\":true"), "git={git} {mine}");
            // Another agent takes b.py from the index lock too.
            let theirs = json::to_string(
                &apply(
                    &mut state, &root, "acquire", "b.py", "agent-b", 1_003, false,
                )
                .unwrap(),
            );
            assert!(theirs.contains("\"acquired\":true"), "git={git} {theirs}");
            // No conflict is logged for taking an index lock.
            let log = json::to_string(
                &apply(&mut state, &root, "conflicts", "", "", 1_003, false).unwrap(),
            );
            assert!(log.contains("\"conflicts\":[]"), "{log}");
            // Re-indexing keeps both explicit locks.
            super::hold_auto(&mut state.locks, &paths, "stele-context-mcp-1", 1_004);
            super::hold_shared(&root, &paths, "stele-context-mcp-1", 1_004);
            let held = json::to_string(
                &apply(&mut state, &root, "status", "a.py", "", 1_005, false).unwrap(),
            );
            assert!(
                held.contains("\"locked_by\":\"agent-a\""),
                "git={git} {held}"
            );
            assert!(!held.contains("auto"), "{held}");
            // An explicit lock still refuses another agent, and its owner
            // can refresh it.
            let refused = json::to_string(
                &apply(
                    &mut state, &root, "acquire", "a.py", "agent-b", 1_006, false,
                )
                .unwrap(),
            );
            assert!(refused.contains("\"acquired\":false"), "{refused}");
            let again = json::to_string(
                &apply(
                    &mut state, &root, "acquire", "a.py", "agent-a", 1_007, false,
                )
                .unwrap(),
            );
            assert!(again.contains("\"acquired\":true"), "{again}");
            let _ = fs::remove_dir_all(&root);
        }
    }

    #[test]
    fn force_steal_is_visible_from_the_other_worktree() {
        let root = std::env::temp_dir().join(format!("stele-lock-wt-{}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        let main = root.join("main");
        let other = root.join("other");
        fs::create_dir_all(main.join(".git")).unwrap();
        fs::create_dir_all(&other).unwrap();
        fs::write(other.join(".git"), "gitdir: ../main/.git\n").unwrap();
        let mut left = State::default();
        let mut right = State::default();
        apply(&mut left, &main, "acquire", "a.py", "agentA", 5_000, false).unwrap();
        let blocked = apply(
            &mut right, &other, "acquire", "a.py", "agentB", 5_010, false,
        )
        .unwrap();
        assert!(json::to_string(&blocked).contains("agentA"));
        apply(&mut right, &other, "acquire", "a.py", "agentB", 5_020, true).unwrap();
        let from_left = apply(&mut left, &main, "status", "a.py", "", 5_030, false).unwrap();
        let from_right = apply(&mut right, &other, "status", "a.py", "", 5_030, false).unwrap();
        let left_text = json::to_string(&from_left);
        let right_text = json::to_string(&from_right);
        assert!(left_text.contains("agentB"), "{left_text}");
        assert!(right_text.contains("agentB"), "{right_text}");
        let log = apply(&mut left, &main, "conflicts", "a.py", "", 5_030, false).unwrap();
        let log_text = json::to_string(&log);
        assert!(log_text.contains("lock_stolen"), "{log_text}");
        assert!(log_text.contains("agentA"), "{log_text}");
        assert!(log_text.contains("agentB"), "{log_text}");
        let stale = apply(&mut left, &main, "release", "a.py", "agentA", 5_040, false).unwrap();
        assert!(json::to_string(&stale).contains("not_owner"));
        let released = apply(
            &mut right, &other, "release", "a.py", "agentB", 5_040, false,
        )
        .unwrap();
        assert!(json::to_string(&released).contains("\"released\":true"));
        let _ = fs::remove_dir_all(&root);
    }
}

struct Shared {
    path: PathBuf,
    locks: Vec<crate::store::Lock>,
    conflicts: Vec<Conflict>,
    notifications: Vec<Value>,
    agents: Vec<Value>,
}

impl Shared {
    fn load(dir: &Path) -> Option<Shared> {
        fs::create_dir_all(dir).ok()?;
        let path = dir.join("coordination.json");
        let text = fs::read_to_string(&path).unwrap_or_else(|_| "{}".to_string());
        let value = json::parse(&text).unwrap_or(Value::object(vec![]));
        let locks = match field(&value, "locks") {
            Some(Value::Array(rows)) => rows.iter().map(lock_from).collect(),
            _ => Vec::new(),
        };
        let conflicts = match field(&value, "conflicts") {
            Some(Value::Array(rows)) => rows
                .iter()
                .map(|row| Conflict {
                    path: text_field(row, "path"),
                    holder: text_field(row, "holder"),
                    requester: text_field(row, "requester"),
                    at: int_field(row, "at"),
                    kind: {
                        let kind = text_field(row, "kind");
                        if kind.is_empty() {
                            "lock_stolen".to_string()
                        } else {
                            kind
                        }
                    },
                })
                .collect(),
            _ => Vec::new(),
        };
        let notifications = match field(&value, "notifications") {
            Some(Value::Array(rows)) => rows.clone(),
            _ => Vec::new(),
        };
        let agents = match field(&value, "agents") {
            Some(Value::Array(rows)) => rows.clone(),
            _ => Vec::new(),
        };
        Some(Shared {
            path,
            locks,
            conflicts,
            notifications,
            agents,
        })
    }

    fn live(&self, path: &str, now: i64) -> Option<(String, i64, bool)> {
        self.locks
            .iter()
            .find(|lock| lock.path == path && lock.until > now)
            .map(|lock| (lock.agent.clone(), lock.until, lock.auto))
    }

    fn save(&self) {
        let body = json::to_string(&Value::object(vec![
            (
                "locks",
                Value::Array(
                    self.locks
                        .iter()
                        .map(|lock| {
                            let mut pairs = vec![
                                ("path", Value::string(&lock.path)),
                                ("agent", Value::string(&lock.agent)),
                                ("until", Value::Int(lock.until)),
                            ];
                            if lock.auto {
                                pairs.push(("auto", Value::Bool(true)));
                            }
                            Value::object(pairs)
                        })
                        .collect(),
                ),
            ),
            (
                "conflicts",
                Value::Array(
                    self.conflicts
                        .iter()
                        .map(|row| {
                            Value::object(vec![
                                ("path", Value::string(&row.path)),
                                ("holder", Value::string(&row.holder)),
                                ("requester", Value::string(&row.requester)),
                                ("at", Value::Int(row.at)),
                                ("kind", Value::string(&row.kind)),
                            ])
                        })
                        .collect(),
                ),
            ),
            ("notifications", Value::Array(self.notifications.clone())),
            ("agents", Value::Array(self.agents.clone())),
        ]));
        let _ = fs::write(&self.path, body);
    }
}

fn lock_from(value: &Value) -> crate::store::Lock {
    crate::store::Lock {
        path: text_field(value, "path"),
        agent: text_field(value, "agent"),
        until: int_field(value, "until"),
        auto: matches!(field(value, "auto"), Some(Value::Bool(true))),
    }
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

fn text_field(value: &Value, key: &str) -> String {
    match field(value, key) {
        Some(Value::String(text)) => text.clone(),
        _ => String::new(),
    }
}

fn int_field(value: &Value, key: &str) -> i64 {
    match field(value, key) {
        Some(Value::Int(number)) => *number,
        _ => 0,
    }
}

/// Index-time locks on `paths` for `agent`, in one read and one write of
/// the coordination file. A live explicit lock is left alone.
pub fn hold_shared(start: &Path, paths: &[String], agent: &str, now: i64) {
    if paths.is_empty() {
        return;
    }
    let Some(dir) = coordination_dir(start) else {
        return;
    };
    let Some(mut shared) = Shared::load(&dir) else {
        return;
    };
    hold_auto(&mut shared.locks, paths, agent, now);
    shared.save();
}

/// Put an index-time lock on each path that has no live explicit lock.
pub fn hold_auto(locks: &mut Vec<crate::store::Lock>, paths: &[String], agent: &str, now: i64) {
    let claimed: std::collections::HashSet<String> = locks
        .iter()
        .filter(|lock| lock.until > now && !lock.auto)
        .map(|lock| lock.path.clone())
        .collect();
    let fresh: std::collections::HashSet<&String> = paths
        .iter()
        .filter(|path| !claimed.contains(*path))
        .collect();
    locks.retain(|lock| !fresh.contains(&lock.path));
    for path in paths {
        if fresh.contains(path) {
            locks.push(crate::store::Lock {
                path: path.clone(),
                agent: agent.to_string(),
                until: now + TTL,
                auto: true,
            });
        }
    }
}

pub fn register_agent(start: &Path, agent: &str, worktree: &str) {
    let Some(dir) = coordination_dir(start) else {
        return;
    };
    let Some(mut shared) = Shared::load(&dir) else {
        return;
    };
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0);
    if let Some(row) = shared
        .agents
        .iter_mut()
        .find(|row| text_field(row, "agent_id") == agent)
    {
        if let Value::Object(pairs) = row {
            for (key, value) in [
                ("status", Value::string("active")),
                ("worktree_root", Value::string(worktree)),
                ("last_heartbeat", Value::Float(now)),
            ] {
                if let Some((_, item)) = pairs.iter_mut().find(|(name, _)| name == key) {
                    *item = value;
                } else {
                    pairs.push((key.to_string(), value));
                }
            }
        }
    } else {
        shared.agents.push(Value::object(vec![
            ("agent_id", Value::string(agent)),
            ("worktree_root", Value::string(worktree)),
            ("status", Value::string("active")),
            ("last_heartbeat", Value::Float(now)),
            ("started_at", Value::Float(now)),
            ("pid", Value::Int(std::process::id() as i64)),
        ]));
    }
    shared.save();
}

pub fn note_indexed(start: &Path, paths: &[String], agent: &str) {
    note_change(start, paths, agent, "indexed")
}

pub fn note_change(start: &Path, paths: &[String], agent: &str, change_type: &str) {
    if paths.is_empty() {
        return;
    }
    let Some(dir) = coordination_dir(start) else {
        return;
    };
    let _ = fs::create_dir_all(&dir);
    let path = dir.join("coordination.json");
    let text = fs::read_to_string(&path).unwrap_or_else(|_| "{}".to_string());
    let mut value = json::parse(&text).unwrap_or(Value::object(vec![]));
    let Value::Object(pairs) = &mut value else {
        return;
    };
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0);
    let worktree = start.display().to_string();
    let mut notes = match pairs.iter().find(|(key, _)| key == "notifications") {
        Some((_, Value::Array(rows))) => rows.clone(),
        _ => Vec::new(),
    };
    for (offset, doc) in paths.iter().enumerate() {
        notes.push(Value::object(vec![
            ("id", Value::Int((notes.len() + 1) as i64)),
            ("document_path", Value::string(doc)),
            ("change_type", Value::string(change_type)),
            ("agent_id", Value::string(agent)),
            (
                "worktree_root",
                if agent.is_empty() {
                    Value::Null
                } else {
                    Value::string(&worktree)
                },
            ),
            ("created_at", Value::Float(now + offset as f64 * 0.000001)),
        ]));
    }
    if let Some((_, item)) = pairs.iter_mut().find(|(key, _)| key == "notifications") {
        *item = Value::Array(notes);
    } else {
        pairs.push(("notifications".to_string(), Value::Array(notes)));
    }
    let _ = fs::write(&path, json::to_string(&value));
}

pub fn coordination_dir(start: &Path) -> Option<PathBuf> {
    let mut dir = start.to_path_buf();
    loop {
        let git = dir.join(".git");
        let kind = fs::metadata(&git).ok();
        if kind.as_ref().is_some_and(|meta| meta.is_dir()) {
            return Some(common_of(&git).join("stele-context"));
        }
        if kind.as_ref().is_some_and(|meta| meta.is_file()) {
            let text = fs::read_to_string(&git).ok()?;
            let rest = text.trim().strip_prefix("gitdir:")?;
            let gitdir = PathBuf::from(rest.trim());
            let gitdir = if gitdir.is_absolute() {
                gitdir
            } else {
                dir.join(gitdir)
            };
            return Some(common_of(&gitdir).join("stele-context"));
        }
        if !dir.pop() {
            return None;
        }
    }
}

fn common_of(gitdir: &Path) -> PathBuf {
    if let Ok(text) = fs::read_to_string(gitdir.join("commondir")) {
        return gitdir.join(text.trim());
    }
    gitdir.to_path_buf()
}
