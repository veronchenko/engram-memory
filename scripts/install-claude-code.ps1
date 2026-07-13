<#
.SYNOPSIS
    Installs Engram's Claude Code integration (hooks + ENGRAM_SPEC.md + ENGRAM_TEMPLATES.md + onboarder agent) into ~/.claude.

.DESCRIPTION
    Windows-only installer for the Claude Code client. Other agents (Codex, etc.) are not
    supported yet. Does NOT register the MCP server itself or wire hooks.json into
    settings.json — see hooks/README.md and the main README's Quick Start for those
    remaining manual steps.

.PARAMETER ClaudeHome
    Root Claude Code config directory. Defaults to $env:USERPROFILE\.claude.

.EXAMPLE
    powershell -File scripts/install-claude-code.ps1
#>
param(
    [string]$ClaudeHome = (Join-Path $env:USERPROFILE ".claude")
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$hooksSource = Join-Path $repoRoot "hooks"

if (-not (Test-Path $hooksSource)) {
    throw "hooks/ source folder not found at $hooksSource"
}

Write-Host "Claude home: $ClaudeHome"

# 1. Ensure ~/.claude and ~/.claude/engram/knowledge exist
if (-not (Test-Path $ClaudeHome)) {
    Write-Host "Creating $ClaudeHome"
    New-Item -ItemType Directory -Path $ClaudeHome -Force | Out-Null
}

$knowledgeDir = Join-Path $ClaudeHome "engram\knowledge"
if (-not (Test-Path $knowledgeDir)) {
    Write-Host "Creating knowledge base directory: $knowledgeDir"
    New-Item -ItemType Directory -Path $knowledgeDir -Force | Out-Null
} else {
    Write-Host "Knowledge base directory already exists: $knowledgeDir"
}

# 2. Copy the hooks/ folder as-is (scripts assume they live at ~/.claude/hooks)
$hooksDest = Join-Path $ClaudeHome "hooks"
Write-Host "Copying hooks/ -> $hooksDest"
New-Item -ItemType Directory -Path $hooksDest -Force | Out-Null
Copy-Item -Path (Join-Path $hooksSource "*") -Destination $hooksDest -Recurse -Force

# 3. Copy ENGRAM_SPEC.md, ENGRAM_TEMPLATES.md and the onboarder agent to their live locations
$engramSpecDest = Join-Path $ClaudeHome "ENGRAM_SPEC.md"
Write-Host "Copying ENGRAM_SPEC.md -> $engramSpecDest"
Copy-Item -Path (Join-Path $hooksSource "ENGRAM_SPEC.md") -Destination $engramSpecDest -Force

$engramTemplatesDest = Join-Path $ClaudeHome "ENGRAM_TEMPLATES.md"
Write-Host "Copying ENGRAM_TEMPLATES.md -> $engramTemplatesDest"
Copy-Item -Path (Join-Path $hooksSource "ENGRAM_TEMPLATES.md") -Destination $engramTemplatesDest -Force

$agentsDir = Join-Path $ClaudeHome "agents"
if (-not (Test-Path $agentsDir)) {
    New-Item -ItemType Directory -Path $agentsDir -Force | Out-Null
}
$onboarderDest = Join-Path $agentsDir "engram-project-onboarder.md"
Write-Host "Copying engram-project-onboarder.md -> $onboarderDest"
Copy-Item -Path (Join-Path $hooksSource "engram-project-onboarder.md") -Destination $onboarderDest -Force

# 4. Reference ENGRAM_SPEC.md from CLAUDE.md via an @-import
$claudeMdPath = Join-Path $ClaudeHome "CLAUDE.md"
$importLine = "@ENGRAM_SPEC.md"

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

Write-Host ""
Write-Host "Done. Still manual:"
Write-Host "  - Register the Engram MCP server (see README.md Quick Start), pointing it at $knowledgeDir"
Write-Host "  - Merge hooks/hooks.json's PreToolUse/SessionStart/Stop/SessionEnd handlers into $ClaudeHome\settings.json (see hooks/README.md)"
