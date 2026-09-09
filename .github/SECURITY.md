# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 2.x     | ✅ Yes    |
| 1.x     | ❌ No     |

## Reporting a Vulnerability

If you discover a security vulnerability in FOLDR, please **do not** open a public issue.

Instead, report it privately by emailing: **foldr@qasimio.me**

Please include:
- A description of the vulnerability
- Steps to reproduce it
- The potential impact
- Your suggested fix (if any)

You will receive a response within 72 hours. If the vulnerability is confirmed, a patch will be released as quickly as possible and you will be credited in the changelog.

## Scope

FOLDR is a local file organizer — it reads and moves files on the local filesystem only. It does not make network requests, store credentials, or run with elevated privileges. Security concerns are most likely to involve:

- Path traversal when organizing directories with unusual names
- Race conditions during file moves
- Log file contents (watch logs are written to `~/.foldr/watch_logs/`)