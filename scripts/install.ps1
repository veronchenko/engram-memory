<#
.SYNOPSIS
    Sets up Engram for whichever of Claude Code / Codex is installed.

.DESCRIPTION
    Windows installer.
    1. Detects `claude` and/or `codex` on PATH.
    2. For each detected CLI, uses *its own* CLI to register the Engram MCP
       server and install the engram-hooks plugin (marketplace add + install).
    3. For each detected CLI, does the same follow-up regardless of which one
       it is: creates an `engram\` folder under that client's home dir holding
       ENGRAM_SPEC.md + ENGRAM_TEMPLATES.md, then wires it into that client's
       global instructions file - `@engram/ENGRAM_SPEC.md` import for Claude
       Code's CLAUDE.md, or (Codex has no `@`-import) the resolved content
       inlined into Codex's AGENTS.md between idempotent markers.

    Best-effort: a failing CLI subcommand (docker missing, network fetch, a
    server/plugin that's already registered) prints a warning and the script
    keeps going - this is provisioning, not a transaction.

.PARAMETER ClaudeHome
    Root Claude Code config directory. Defaults to $env:USERPROFILE\.claude.

.PARAMETER CodexHome
    Root Codex config directory. Defaults to $env:USERPROFILE\.codex.

.EXAMPLE
    powershell -File scripts/install.ps1
#>
param(
    [string]$ClaudeHome = (Join-Path $env:USERPROFILE ".claude"),
    [string]$CodexHome = (Join-Path $env:USERPROFILE ".codex")
)

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$pluginSource = Join-Path $repoRoot "plugins\engram-hooks"

if (-not (Test-Path $pluginSource)) {
    throw "plugins/engram-hooks/ source folder not found at $pluginSource"
}

$hasClaude = [bool](Get-Command claude -ErrorAction SilentlyContinue)
$hasCodex = [bool](Get-Command codex -ErrorAction SilentlyContinue)

if (-not $hasClaude -and -not $hasCodex) {
    Write-Error "Neither 'claude' nor 'codex' found on PATH - nothing to wire up. Install one of them first, then re-run this script."
    exit 1
}

Write-Host "Detected: claude=$hasClaude, codex=$hasCodex"

# --- 1. Knowledge base directory (shared, client-agnostic) ------------------

$knowledgeDir = Join-Path $env:USERPROFILE ".engram\knowledge"
$oldClaudeKnowledgeDir = Join-Path $ClaudeHome "engram\knowledge"

if (-not (Test-Path $knowledgeDir) -and (Test-Path $oldClaudeKnowledgeDir)) {
    Write-Host "Migrating knowledge base: $oldClaudeKnowledgeDir -> $knowledgeDir"
    New-Item -ItemType Directory -Path (Split-Path $knowledgeDir -Parent) -Force | Out-Null
    Move-Item -Path $oldClaudeKnowledgeDir -Destination $knowledgeDir
} elseif (-not (Test-Path $knowledgeDir)) {
    Write-Host "Creating knowledge base directory: $knowledgeDir"
    New-Item -ItemType Directory -Path $knowledgeDir -Force | Out-Null
} else {
    Write-Host "Knowledge base directory already exists: $knowledgeDir"
}

$mcpCommand = @("docker", "run", "-i", "--rm", "-v", "${knowledgeDir}:/knowledge", "foreigndmitryi/engram")

# --- 2. Per-client MCP server + plugin install (each CLI's own commands) ----

if ($hasClaude) {
    Write-Host ""
    Write-Host "-- Claude Code --"
    & claude mcp add --transport stdio --scope user engram -- @mcpCommand
    if ($LASTEXITCODE -eq 0) { Write-Host "Registered engram MCP server" }
    else { Write-Warning "'claude mcp add' failed or engram is already registered - check with 'claude mcp list'" }

    & claude plugin marketplace add $repoRoot
    if ($LASTEXITCODE -eq 0) { Write-Host "Added engram-memory marketplace" }
    else { Write-Warning "'claude plugin marketplace add' failed" }

    & claude plugin install engram-hooks@engram-memory --scope user
    if ($LASTEXITCODE -eq 0) { Write-Host "Installed engram-hooks plugin" }
    else { Write-Warning "'claude plugin install' failed - try '/plugin install engram-hooks@engram-memory' inside a session" }
}

if ($hasCodex) {
    Write-Host ""
    Write-Host "-- Codex --"
    & codex mcp add engram -- @mcpCommand
    if ($LASTEXITCODE -eq 0) { Write-Host "Registered engram MCP server" }
    else { Write-Warning "'codex mcp add' failed or engram is already registered - check with 'codex mcp list'" }

    & codex plugin marketplace add (Join-Path $repoRoot "plugins")
    if ($LASTEXITCODE -eq 0) { Write-Host "Added engram-memory marketplace" }
    else { Write-Warning "'codex plugin marketplace add' failed" }

    & codex plugin add engram-hooks@engram-memory
    if ($LASTEXITCODE -eq 0) { Write-Host "Installed engram-hooks plugin" }
    else { Write-Warning "'codex plugin add' failed" }
}

# --- 3. Global instructions, same shape for both clients --------------------

if ($hasClaude) {
    Write-Host ""
    Write-Host "-- Claude Code global instructions --"
    $claudeEngramDir = Join-Path $ClaudeHome "engram"
    New-Item -ItemType Directory -Path $claudeEngramDir -Force | Out-Null
    Copy-Item -Path (Join-Path $pluginSource "ENGRAM_SPEC.md") -Destination (Join-Path $claudeEngramDir "ENGRAM_SPEC.md") -Force
    Copy-Item -Path (Join-Path $pluginSource "ENGRAM_TEMPLATES.md") -Destination (Join-Path $claudeEngramDir "ENGRAM_TEMPLATES.md") -Force
    Write-Host "Copied ENGRAM_SPEC.md + ENGRAM_TEMPLATES.md -> $claudeEngramDir"

    $claudeMdPath = Join-Path $ClaudeHome "CLAUDE.md"
    $importLine = "@engram/ENGRAM_SPEC.md"

    if (-not (Test-Path $claudeMdPath)) {
        Write-Host "Creating $claudeMdPath with $importLine"
        Set-Content -Path $claudeMdPath -Value $importLine -Encoding utf8
    } else {
        $existing = Get-Content -Path $claudeMdPath -Raw
        if ($existing -match [regex]::Escape($importLine)) {
            Write-Host "$claudeMdPath already references $importLine"
        } else {
            Write-Host "Adding $importLine to top of $claudeMdPath"
            Set-Content -Path $claudeMdPath -Value ($importLine + "`n`n" + $existing) -Encoding utf8
        }
    }
}

if ($hasCodex) {
    Write-Host ""
    Write-Host "-- Codex global instructions --"
    $codexEngramDir = Join-Path $CodexHome "engram"
    New-Item -ItemType Directory -Path $codexEngramDir -Force | Out-Null
    Copy-Item -Path (Join-Path $pluginSource "ENGRAM_SPEC.md") -Destination (Join-Path $codexEngramDir "ENGRAM_SPEC.md") -Force
    Copy-Item -Path (Join-Path $pluginSource "ENGRAM_TEMPLATES.md") -Destination (Join-Path $codexEngramDir "ENGRAM_TEMPLATES.md") -Force
    Write-Host "Copied ENGRAM_SPEC.md + ENGRAM_TEMPLATES.md -> $codexEngramDir (reference copies; Codex reads the inlined block below, not these files)"

    # Codex's AGENTS.md has no @-import mechanism (unlike Claude Code's CLAUDE.md),
    # so the two files are concatenated here, with ENGRAM_SPEC.md's own
    # "@ENGRAM_TEMPLATES.md" import line replaced by the templates' actual content.
    $beginMarker = "<!-- BEGIN engram-memory (auto-generated by engram_memory/scripts/install.ps1 -- do not edit by hand) -->"
    $endMarker = "<!-- END engram-memory -->"

    $specContent = Get-Content -Path (Join-Path $pluginSource "ENGRAM_SPEC.md") -Raw
    $templatesContent = Get-Content -Path (Join-Path $pluginSource "ENGRAM_TEMPLATES.md") -Raw
    $mergedContent = $specContent.Replace("@ENGRAM_TEMPLATES.md", $templatesContent)

    $agentsMdPath = Join-Path $CodexHome "AGENTS.md"
    $block = "$beginMarker`n`n$mergedContent`n`n$endMarker"

    if (-not (Test-Path $agentsMdPath)) {
        Write-Host "Creating $agentsMdPath with the engram-memory block"
        Set-Content -Path $agentsMdPath -Value $block -Encoding utf8
    } else {
        $existing = Get-Content -Path $agentsMdPath -Raw
        if ($existing -match [regex]::Escape($beginMarker)) {
            Write-Host "Updating existing engram-memory block in $agentsMdPath"
            $pattern = [regex]::Escape($beginMarker) + "(?s).*?" + [regex]::Escape($endMarker)
            $rest = [regex]::Replace($existing, $pattern, "").TrimStart()
            Set-Content -Path $agentsMdPath -Value ($block + "`n`n" + $rest) -Encoding utf8
        } else {
            Write-Host "Adding engram-memory block to top of $agentsMdPath"
            Set-Content -Path $agentsMdPath -Value ($block + "`n`n" + $existing) -Encoding utf8
        }
    }
}

Write-Host ""
Write-Host "Done. Still manual:"
Write-Host "  - Disable Claude Code's built-in auto memory (see plugins/engram-hooks/README.md, step 3)"
Write-Host "  - Sanity-check the install (see plugins/engram-hooks/README.md)"
