# Request: Grok Build Release Automation Capabilities

**Date:** 2026-05-15  
**Project:** stele-context  
**Status:** Ready for implementation

## Goal

Enable **Grok Build agents** to perform safe, fully automated releases of `stele-context` (and similar packages) with minimal human intervention, while maintaining security and auditability.

## Current State

- We have excellent local tooling (`scripts/release.py`, dual-trigger `publish.yml`).
- The missing pieces are on the **Grok platform / MCP side** and **GitHub repository permissions**.

## Required Capabilities

### 1. New MCP Tool: `create_release` (Highest Priority)

**Proposed Tool Name:** `grok_com_github__create_release`

**Input Schema (suggested):**

```json
{
  "owner": { "type": "string", "description": "Repository owner/org" },
  "repo": { "type": "string" },
  "tag_name": { "type": "string", "description": "e.g. 'v1.3.3'" },
  "name": { "type": "string", "description": "Release title" },
  "body": { "type": "string", "description": "Markdown release notes" },
  "draft": { "type": "boolean", "default": false },
  "prerelease": { "type": "boolean", "default": false },
  "generate_release_notes": { "type": "boolean", "default": false }
}
```

**Output:** The created release object (including `html_url`, `id`, etc.).

**Why needed:**
- Allows a Grok Build agent to create a rich, reviewed GitHub Release after running the local release script.
- Complements the existing read-only release tools (`get_latest_release`, `list_releases`, `get_release_by_tag`).

**Alternative (if preferred):** A generic `call_github_api` tool with method + path + body would also work.

### 2. GitHub Repository Permissions for Grok Build Agents

**Repository:** `IronAdamant/stele-context` (and future packages)

**Required permissions for the Grok Build service identity:**

| Permission          | Level  | Purpose                              | Notes |
|---------------------|--------|--------------------------------------|-------|
| Contents            | Write  | Push tags, create releases           | Essential |
| Actions             | Read   | Inspect workflow runs                | Useful |
| Pull Requests       | Write  | (Optional) Open version bump PRs     | Nice to have |
| Metadata            | Read   | Basic repo info                      | Required |

**Recommended implementation:**
- Create a **GitHub App** (or Fine-Grained Personal Access Token) specifically for Grok Build release automation.
- Scope it only to the necessary repositories.
- Use short-lived tokens via the GitHub API when possible.

### 3. Optional but Recommended Enhancements

- Support for `update_release` (to edit release notes after creation).
- Ability to add release assets.
- Webhook / notification when a release is published (for agent feedback loops).

## Benefits

- True end-to-end autonomous releases by Grok Build.
- Consistent, high-quality releases with proper changelogs and testing gates.
- Reduced maintainer toil.
- Better audit trail (every release performed by an identifiable agent).

## Contact / Next Steps

This request was prepared as part of active development on the `stele-context` project during a Grok Build session.

We are ready to test as soon as the `create_release` tool is available.

---

**Prepared by:** Grok (as Grok Build agent)  
**Files demonstrating readiness:**
- `scripts/release.py`
- `.github/workflows/publish.yml` (tag-triggered)
- `docs/release-automation.md`
- `stele_context/cli_release.py` (CLI integration)

Please let us know when the tool is ready or if you need any adjustments to the schema.
