# Security Policy

## Supported Versions

Only the latest tagged release (see `CHANGELOG.md`/`VERSION`) receives security fixes. There is no long-term-support branch.

## Reporting a Vulnerability

Please **do not** open a public GitHub issue for security vulnerabilities. Instead, use [GitHub's private vulnerability reporting](https://github.com/veronchenko/engram-memory/security/advisories/new) for this repository.

Include:
- A description of the vulnerability and its impact
- Steps to reproduce (transport used: stdio/SSE/streamable-http, and whether multi-tenant mode is involved)
- Any relevant logs or PoC

You should receive an initial response within a few days. Once a fix is available, it will be released and credited in the changelog unless you request otherwise.

## Scope Notes

- Engram's multi-tenant mode (`ENGRAM_MULTI_TENANT=1`) uses a static pre-shared API key per team, hashed at rest in `admin.db` — treat key distribution as sensitive.
- The admin API (`src/admin_api/`) is loopback-only by design and must not be exposed outside the container/host network.
