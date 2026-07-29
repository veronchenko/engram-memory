#!/usr/bin/env bash
# Sets up Engram for whichever of Claude Code / Codex is installed:
#   1. Detects `claude` and/or `codex` on PATH.
#   2. For each detected CLI, uses *its own* CLI to register the Engram MCP
#      server and install the engram-hooks plugin (marketplace add + install).
#   3. For each detected CLI, does the same follow-up regardless of which one
#      it is: creates an `engram/` folder under that client's home dir holding
#      ENGRAM_SPEC.md + ENGRAM_TEMPLATES.md, then wires it into that client's
#      global instructions file — `@engram/ENGRAM_SPEC.md` import for Claude
#      Code's CLAUDE.md, or (Codex has no `@`-import) the resolved content
#      inlined into Codex's AGENTS.md between idempotent markers.
#
# Best-effort: a failing CLI subcommand (docker missing, network fetch, a
# server/plugin that's already registered) prints a warning and the script
# keeps going — this is provisioning, not a transaction.
#
# Usage: scripts/install.sh [claude_home] [codex_home]
#   claude_home defaults to $HOME/.claude, codex_home to $HOME/.codex

set -uo pipefail

CLAUDE_HOME="${1:-$HOME/.claude}"
CODEX_HOME="${2:-$HOME/.codex}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLUGIN_SOURCE="$REPO_ROOT/plugins/engram-hooks"

if [ ! -d "$PLUGIN_SOURCE" ]; then
    echo "plugins/engram-hooks/ source folder not found at $PLUGIN_SOURCE" >&2
    exit 1
fi

HAS_CLAUDE=0
command -v claude >/dev/null 2>&1 && HAS_CLAUDE=1
HAS_CODEX=0
command -v codex >/dev/null 2>&1 && HAS_CODEX=1

if [ "$HAS_CLAUDE" -eq 0 ] && [ "$HAS_CODEX" -eq 0 ]; then
    echo "Neither 'claude' nor 'codex' found on PATH — nothing to wire up." >&2
    echo "Install one of them first, then re-run this script." >&2
    exit 1
fi

echo "Detected: claude=$([ "$HAS_CLAUDE" -eq 1 ] && echo yes || echo no), codex=$([ "$HAS_CODEX" -eq 1 ] && echo yes || echo no)"

# --- 1. Knowledge base directory (shared, client-agnostic) ------------------

KNOWLEDGE_DIR="$HOME/.engram/knowledge"
OLD_CLAUDE_KNOWLEDGE_DIR="$CLAUDE_HOME/engram/knowledge"

if [ ! -d "$KNOWLEDGE_DIR" ] && [ -d "$OLD_CLAUDE_KNOWLEDGE_DIR" ]; then
    echo "Migrating knowledge base: $OLD_CLAUDE_KNOWLEDGE_DIR -> $KNOWLEDGE_DIR"
    mkdir -p "$(dirname "$KNOWLEDGE_DIR")"
    mv "$OLD_CLAUDE_KNOWLEDGE_DIR" "$KNOWLEDGE_DIR"
elif [ ! -d "$KNOWLEDGE_DIR" ]; then
    echo "Creating knowledge base directory: $KNOWLEDGE_DIR"
    mkdir -p "$KNOWLEDGE_DIR"
else
    echo "Knowledge base directory already exists: $KNOWLEDGE_DIR"
fi

MCP_COMMAND=(docker run -i --rm -v "$KNOWLEDGE_DIR:/knowledge" foreigndmitryi/engram)

# --- 2. Per-client MCP server + plugin install (each CLI's own commands) ----

if [ "$HAS_CLAUDE" -eq 1 ]; then
    echo ""
    echo "-- Claude Code --"
    claude mcp add --transport stdio --scope user engram -- "${MCP_COMMAND[@]}" \
        && echo "Registered engram MCP server" \
        || echo "warn: 'claude mcp add' failed or engram is already registered — check with 'claude mcp list'" >&2

    claude plugin marketplace add "$REPO_ROOT" \
        && echo "Added engram-memory marketplace" \
        || echo "warn: 'claude plugin marketplace add' failed" >&2

    claude plugin install engram-hooks@engram-memory --scope user \
        && echo "Installed engram-hooks plugin" \
        || echo "warn: 'claude plugin install' failed — try '/plugin install engram-hooks@engram-memory' inside a session" >&2
fi

if [ "$HAS_CODEX" -eq 1 ]; then
    echo ""
    echo "-- Codex --"
    codex mcp add engram -- "${MCP_COMMAND[@]}" \
        && echo "Registered engram MCP server" \
        || echo "warn: 'codex mcp add' failed or engram is already registered — check with 'codex mcp list'" >&2

    codex plugin marketplace add "$REPO_ROOT/plugins" \
        && echo "Added engram-memory marketplace" \
        || echo "warn: 'codex plugin marketplace add' failed" >&2

    codex plugin add engram-hooks@engram-memory \
        && echo "Installed engram-hooks plugin" \
        || echo "warn: 'codex plugin add' failed" >&2
fi

# --- 3. Global instructions, same shape for both clients --------------------

if [ "$HAS_CLAUDE" -eq 1 ]; then
    echo ""
    echo "-- Claude Code global instructions --"
    CLAUDE_ENGRAM_DIR="$CLAUDE_HOME/engram"
    mkdir -p "$CLAUDE_ENGRAM_DIR"
    cp -f "$PLUGIN_SOURCE/ENGRAM_SPEC.md" "$CLAUDE_ENGRAM_DIR/ENGRAM_SPEC.md"
    cp -f "$PLUGIN_SOURCE/ENGRAM_TEMPLATES.md" "$CLAUDE_ENGRAM_DIR/ENGRAM_TEMPLATES.md"
    echo "Copied ENGRAM_SPEC.md + ENGRAM_TEMPLATES.md -> $CLAUDE_ENGRAM_DIR"

    CLAUDE_MD_PATH="$CLAUDE_HOME/CLAUDE.md"
    IMPORT_LINE="@engram/ENGRAM_SPEC.md"

    if [ ! -f "$CLAUDE_MD_PATH" ]; then
        echo "Creating $CLAUDE_MD_PATH with $IMPORT_LINE"
        printf '%s\n' "$IMPORT_LINE" > "$CLAUDE_MD_PATH"
    elif grep -qF "$IMPORT_LINE" "$CLAUDE_MD_PATH"; then
        echo "$CLAUDE_MD_PATH already references $IMPORT_LINE"
    else
        echo "Adding $IMPORT_LINE to top of $CLAUDE_MD_PATH"
        tmp_file="$(mktemp)"
        { printf '%s\n\n' "$IMPORT_LINE"; cat "$CLAUDE_MD_PATH"; } > "$tmp_file"
        mv "$tmp_file" "$CLAUDE_MD_PATH"
    fi
fi

if [ "$HAS_CODEX" -eq 1 ]; then
    echo ""
    echo "-- Codex global instructions --"
    CODEX_ENGRAM_DIR="$CODEX_HOME/engram"
    mkdir -p "$CODEX_ENGRAM_DIR"
    cp -f "$PLUGIN_SOURCE/ENGRAM_SPEC.md" "$CODEX_ENGRAM_DIR/ENGRAM_SPEC.md"
    cp -f "$PLUGIN_SOURCE/ENGRAM_TEMPLATES.md" "$CODEX_ENGRAM_DIR/ENGRAM_TEMPLATES.md"
    echo "Copied ENGRAM_SPEC.md + ENGRAM_TEMPLATES.md -> $CODEX_ENGRAM_DIR (reference copies; Codex reads the inlined block below, not these files)"

    # Codex's AGENTS.md has no @-import mechanism (unlike Claude Code's CLAUDE.md),
    # so the two files are concatenated here, with ENGRAM_SPEC.md's own
    # "@ENGRAM_TEMPLATES.md" import line replaced by the templates' actual content.
    BEGIN_MARKER="<!-- BEGIN engram-memory (auto-generated by engram_memory/scripts/install.sh — do not edit by hand) -->"
    END_MARKER="<!-- END engram-memory -->"

    spec_content="$(cat "$PLUGIN_SOURCE/ENGRAM_SPEC.md")"
    templates_content="$(cat "$PLUGIN_SOURCE/ENGRAM_TEMPLATES.md")"
    merged_content="${spec_content/@ENGRAM_TEMPLATES.md/$templates_content}"

    AGENTS_MD_PATH="$CODEX_HOME/AGENTS.md"
    block_file="$(mktemp)"
    { printf '%s\n\n' "$BEGIN_MARKER"; printf '%s\n\n' "$merged_content"; printf '%s\n' "$END_MARKER"; } > "$block_file"

    if [ ! -f "$AGENTS_MD_PATH" ]; then
        echo "Creating $AGENTS_MD_PATH with the engram-memory block"
        cp "$block_file" "$AGENTS_MD_PATH"
    elif grep -qF "$BEGIN_MARKER" "$AGENTS_MD_PATH"; then
        echo "Updating existing engram-memory block in $AGENTS_MD_PATH"
        tmp_file="$(mktemp)"
        awk -v begin="$BEGIN_MARKER" -v end="$END_MARKER" '
            $0 == begin { skipping = 1; next }
            $0 == end { skipping = 0; next }
            !skipping { print }
        ' "$AGENTS_MD_PATH" > "$tmp_file"
        { cat "$block_file"; printf '\n'; cat "$tmp_file"; } > "$AGENTS_MD_PATH"
        rm -f "$tmp_file"
    else
        echo "Adding engram-memory block to top of $AGENTS_MD_PATH"
        tmp_file="$(mktemp)"
        { cat "$block_file"; printf '\n'; cat "$AGENTS_MD_PATH"; } > "$tmp_file"
        mv "$tmp_file" "$AGENTS_MD_PATH"
    fi
    rm -f "$block_file"
fi

echo ""
echo "Done. Still manual:"
echo "  - Disable Claude Code's built-in auto memory (see plugins/engram-hooks/README.md, step 3)"
echo "  - Sanity-check the install (see plugins/engram-hooks/README.md)"
