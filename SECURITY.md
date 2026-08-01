# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 1.x     | Yes                |
| 0.10.x  | Security fixes only (upgrade to 1.x recommended) |
| < 0.10  | No                 |

## Reporting a Vulnerability

If you discover a security vulnerability in Stele Context, please report it responsibly.

**Do NOT open a public GitHub issue for security vulnerabilities.**

### How to Report

1. **Email**: Send a detailed report to the maintainer via GitHub's private vulnerability reporting feature at https://github.com/IronAdamant/stele-context/security/advisories/new
2. **Include**:
   - Description of the vulnerability
   - Steps to reproduce
   - Potential impact
   - Suggested fix (if any)

### What to Expect

- **Acknowledgment** within 48 hours
- **Assessment** within 7 days
- **Fix or mitigation** within 30 days for confirmed vulnerabilities
- Credit in the release notes (unless you prefer anonymity)

### Scope

The following are in scope:

- SQL injection in storage/query operations
- Path traversal in file indexing or storage
- Denial of service through crafted input (e.g., regex bombs in `search_text`)
- Information disclosure through error messages or logs
- Unsafe deserialization (the project uses JSON only, never pickle)
- Cross-worktree data leakage through coordination DB
- Lock bypass or escalation in multi-agent scenarios

### Out of Scope

- Vulnerabilities in optional dependencies (Pillow, pymupdf, librosa, etc.) — report those upstream
- Issues requiring local filesystem access (the project is designed for local-only use)
- Performance issues (use GitHub Issues instead)

## Security Design

Stele Context is designed with security in mind:

- **No network access**: All operations are 100% local. No data leaves the machine.
- **No pickle**: All serialization uses JSON + zlib. No arbitrary code execution via deserialization.
- **No eval/exec**: No dynamic code execution of user-supplied input.
- **Parameterized SQL**: All database queries use parameterized statements (no string interpolation for values).
- **Per-agent isolation**: Document locks prevent unauthorized cross-agent writes.
- **Local-only storage**: Default storage is per-project (`.stele-context/`), not shared globally.
