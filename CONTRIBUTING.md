# Contributing to Engram

Thanks for considering a contribution. Engram is a small, opinionated MCP server — please read `CLAUDE.md` first for the architecture and conventions before opening a PR; it documents the layout, testing setup, and known gotchas in more detail than this file will.

## Reporting bugs

Open a GitHub issue with:
- What you expected vs. what happened
- Repro steps (MCP client + transport used: stdio/SSE/streamable-http)
- Relevant logs from `doctor` if the issue involves entry/index integrity

## Proposing changes

1. Open an issue first for anything beyond a small fix, so the direction can be agreed before you invest time.
2. Keep changes atomic — one logical change per PR (no drive-by refactors bundled with a feature).
3. Follow the existing code style (see `CLAUDE.md`'s coding standards section).
4. Add or update tests under `tests/` (mirrors `src/` layout).

## Running tests

Tests run in a separate Docker image, built from the production image:

```bash
docker build -t engram .
docker build -f tests/Dockerfile -t engram-test .
docker run --rm engram-test
```

## Pull requests

- Use [Conventional Commits](https://www.conventionalcommits.org/) style messages (`feat:`, `fix:`, `chore:`, `refactor:`, `test:`, `docs:`).
- Describe *why* the change is needed, not just what changed.
- Link the related issue if one exists.
