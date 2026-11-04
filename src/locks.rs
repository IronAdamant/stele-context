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

const TTL: i64 = 300;

pub fn apply(
    state: &mut State,
    cwd: &Path,
    action: &str,
    path: &str,
    agent: &str,
    now: i64,
    force: bool,
) -> Result<Value, String> {
    let mut shared = coordination_dir(cwd).and_then(|dir| Shared::load(&dir));
    let outcome = match action {
        "acquire" => acquire(state, shared.as_mut(), path, agent, now, force),
        "refresh" => refresh(state, shared.as_mut(), path, agent, now),
        "release" => Ok(release(state, shared.as_mut(), path, agent, false, now)),
        "release_all" => Ok(release(state, shared.as_mut(), path, agent, true, now)),
        "status" => Ok(status(state, shared.as_ref(), path, now)),
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
    if let Some((holder, until)) = existing {
        if holder != agent && !force {
            return Ok(refused(&holder, until));
        }
        if holder != agent && force {
            record_conflict(state, &mut shared, path, &holder, agent, now);
        }
    }
    write_lock(state, shared, path, agent, now + TTL);
    let version = state
        .documents
        .iter()
        .find(|doc| doc.path == path)
        .map(|doc| doc.doc_version as i64)
        .unwrap_or(0);
    Ok(Value::object(vec![
        ("acquired", Value::Bool(true)),
        ("doc_version", Value::Int(version)),
        ("locked_by", Value::string(agent)),
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
        Some((holder, _)) if holder == agent => {
            write_lock(state, shared, path, agent, now + TTL);
            Ok(Value::object(vec![("refreshed", Value::Bool(true))]))
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
    });
    if let Some(shared) = shared {
        shared.locks.retain(|lock| lock.path != path);
        shared.locks.push(crate::store::Lock {
            path: path.to_string(),
            agent: agent.to_string(),
            until,
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
    let row = Conflict {
        path: path.to_string(),
        holder: holder.to_string(),
        requester: agent.to_string(),
        at: now,
    };
    state.conflicts.push(row.clone());
    if let Some(shared) = shared.as_mut() {
        shared.conflicts.push(row);
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
        let released = state
            .locks
            .iter()
            .filter(|lock| lock.agent == agent)
            .count();
        state.locks.retain(|lock| lock.agent != agent);
        if let Some(shared) = shared {
            shared.locks.retain(|lock| lock.agent != agent);
        }
        return Value::object(vec![
            ("released", Value::Bool(true)),
            ("count", Value::Int(released as i64)),
        ]);
    }
    let holder = {
        let view = shared.as_deref();
        live_holder(state, view, path, now).map(|(name, _)| name)
    };
    match holder {
        None => Value::object(vec![
            ("released", Value::Bool(false)),
            ("reason", Value::string("not_locked")),
        ]),
        Some(holder) if holder != agent => Value::object(vec![
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

fn status(state: &State, shared: Option<&Shared>, path: &str, now: i64) -> Value {
    match live_holder(state, shared, path, now) {
        None => Value::object(vec![
            ("locked", Value::Bool(false)),
            (
                "doc_version",
                Value::Int(
                    state
                        .documents
                        .iter()
                        .find(|doc| doc.path == path)
                        .map(|doc| doc.doc_version as i64)
                        .unwrap_or(0),
                ),
            ),
        ]),
        Some((holder, until)) => Value::object(vec![
            ("locked", Value::Bool(true)),
            ("locked_by", Value::string(holder)),
            ("locked_at", Value::Int(until - TTL)),
            ("expires_at", Value::Int(until)),
        ]),
    }
}

fn conflicts(state: &State, shared: Option<&Shared>, path: &str) -> Value {
    let mut rows: Vec<Conflict> = state.conflicts.clone();
    if let Some(shared) = shared {
        for row in &shared.conflicts {
            if !rows.iter().any(|have| {
                have.path == row.path && have.requester == row.requester && have.at == row.at
            }) {
                rows.push(row.clone());
            }
        }
    }
    let rendered = rows
        .into_iter()
        .filter(|row| path.is_empty() || row.path == path)
        .map(|row| {
            Value::object(vec![
                ("path", Value::string(row.path)),
                ("holder", Value::string(row.holder)),
                ("requester", Value::string(row.requester)),
                ("at", Value::Int(row.at)),
                ("conflict_type", Value::string("lock_stolen")),
            ])
        })
        .collect();
    Value::object(vec![("conflicts", Value::Array(rendered))])
}

fn reap(state: &mut State, shared: Option<&mut Shared>, now: i64) -> Value {
    let before = state.locks.len();
    state.locks.retain(|lock| lock.until > now);
    let mut reaped = before - state.locks.len();
    if let Some(shared) = shared {
        let shared_before = shared.locks.len();
        shared.locks.retain(|lock| lock.until > now);
        reaped += shared_before - shared.locks.len();
    }
    Value::object(vec![("reaped", Value::Int(reaped as i64))])
}

fn refused(holder: &str, until: i64) -> Value {
    Value::object(vec![
        ("acquired", Value::Bool(false)),
        ("locked_by", Value::string(holder)),
        ("locked_at", Value::Int(until - TTL)),
        ("expires_at", Value::Int(until)),
    ])
}

fn live_holder(
    state: &State,
    shared: Option<&Shared>,
    path: &str,
    now: i64,
) -> Option<(String, i64)> {
    if let Some(lock) = state
        .locks
        .iter()
        .find(|lock| lock.path == path && lock.until > now)
    {
        return Some((lock.agent.clone(), lock.until));
    }
    shared.and_then(|item| item.live(path, now))
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
        let released = apply(
            &mut state, &root, "release", "a.py", "agent-a", 1_030, false,
        )
        .unwrap();
        assert!(json::to_string(&released).contains("not_owner"));
        let released = apply(
            &mut state, &root, "release", "a.py", "agent-b", 1_030, false,
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
                })
                .collect(),
            _ => Vec::new(),
        };
        Some(Shared {
            path,
            locks,
            conflicts,
        })
    }

    fn live(&self, path: &str, now: i64) -> Option<(String, i64)> {
        self.locks
            .iter()
            .find(|lock| lock.path == path && lock.until > now)
            .map(|lock| (lock.agent.clone(), lock.until))
    }

    fn save(&self) {
        let body = json::to_string(&Value::object(vec![
            (
                "locks",
                Value::Array(
                    self.locks
                        .iter()
                        .map(|lock| {
                            Value::object(vec![
                                ("path", Value::string(&lock.path)),
                                ("agent", Value::string(&lock.agent)),
                                ("until", Value::Int(lock.until)),
                            ])
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
                            ])
                        })
                        .collect(),
                ),
            ),
        ]));
        let _ = fs::write(&self.path, body);
    }
}

fn lock_from(value: &Value) -> crate::store::Lock {
    crate::store::Lock {
        path: text_field(value, "path"),
        agent: text_field(value, "agent"),
        until: int_field(value, "until"),
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

pub fn coordination_dir(start: &Path) -> Option<PathBuf> {
    let mut dir = start.to_path_buf();
    loop {
        let git = dir.join(".git");
        if git.is_dir() {
            return Some(common_of(&git).join("stele-context"));
        }
        if git.is_file() {
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
