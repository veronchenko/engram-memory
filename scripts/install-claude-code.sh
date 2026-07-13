#!/usr/bin/env bash
# Installs Engram's Claude Code integration (hooks + ENGRAM_SPEC.md + ENGRAM_TEMPLATES.md
# + onboarder agent) into ~/.claude. macOS/Linux installer for the Claude Code client only — other agents
# (Codex, etc.) are not supported yet.
#
# Does NOT register the MCP server itself or wire hooks.json into settings.json — see
# hooks/README.md and the main README's Quick Start for those remaining manual steps.
#
# Usage: scripts/install-claude-code.sh [claude_home]
#   claude_home defaults to $HOME/.claude

set -euo pipefail

CLAUDE_HOME="${1:-$HOME/.claude}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOOKS_SOURCE="$REPO_ROOT/hooks"

if [ ! -d "$HOOKS_SOURCE" ]; then
    echo "hooks/ source folder not found at $HOOKS_SOURCE" >&2
    exit 1
fi

echo "Claude home: $CLAUDE_HOME"

# 1. Ensure ~/.claude and ~/.claude/engram/knowledge exist
if [ ! -d "$CLAUDE_HOME" ]; then
    echo "Creating $CLAUDE_HOME"
    mkdir -p "$CLAUDE_HOME"
fi

KNOWLEDGE_DIR="$CLAUDE_HOME/engram/knowledge"
if [ ! -d "$KNOWLEDGE_DIR" ]; then
    echo "Creating knowledge base directory: $KNOWLEDGE_DIR"
    mkdir -p "$KNOWLEDGE_DIR"
else
    echo "Knowledge base directory already exists: $KNOWLEDGE_DIR"
fi

# 2. Copy the hooks/ folder as-is (scripts assume they live at ~/.claude/hooks)
HOOKS_DEST="$CLAUDE_HOME/hooks"
echo "Copying hooks/ -> $HOOKS_DEST"
mkdir -p "$HOOKS_DEST"
cp -R "$HOOKS_SOURCE/." "$HOOKS_DEST/"

# 3. Copy ENGRAM_SPEC.md, ENGRAM_TEMPLATES.md and the onboarder agent to their live locations
ENGRAM_SPEC_DEST="$CLAUDE_HOME/ENGRAM_SPEC.md"
echo "Copying ENGRAM_SPEC.md -> $ENGRAM_SPEC_DEST"
cp -f "$HOOKS_SOURCE/ENGRAM_SPEC.md" "$ENGRAM_SPEC_DEST"

ENGRAM_TEMPLATES_DEST="$CLAUDE_HOME/ENGRAM_TEMPLATES.md"
echo "Copying ENGRAM_TEMPLATES.md -> $ENGRAM_TEMPLATES_DEST"
cp -f "$HOOKS_SOURCE/ENGRAM_TEMPLATES.md" "$ENGRAM_TEMPLATES_DEST"

AGENTS_DIR="$CLAUDE_HOME/agents"
mkdir -p "$AGENTS_DIR"
ONBOARDER_DEST="$AGENTS_DIR/engram-project-onboarder.md"
echo "Copying engram-project-onboarder.md -> $ONBOARDER_DEST"
cp -f "$HOOKS_SOURCE/engram-project-onboarder.md" "$ONBOARDER_DEST"

# 4. Reference ENGRAM_SPEC.md from CLAUDE.md via an @-import
CLAUDE_MD_PATH="$CLAUDE_HOME/CLAUDE.md"
IMPORT_LINE="@ENGRAM_SPEC.md"

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

echo ""
echo "Done. Still manual:"
echo "  - Register the Engram MCP server (see README.md Quick Start), pointing it at $KNOWLEDGE_DIR"
echo "  - Merge hooks/hooks.json's PreToolUse/SessionStart/Stop/SessionEnd handlers into $CLAUDE_HOME/settings.json (see hooks/README.md)"
