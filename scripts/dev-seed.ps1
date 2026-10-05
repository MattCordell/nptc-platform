<#
.SYNOPSIS
Seeds a running development stack with the 50-row sample workbook (FR-70, FR-76, NFR-41).

.DESCRIPTION
Runs the transform on the committed sample workbook, drops the entries the baseline loader would
refuse (scripts/dev_seed_filter.py), copies the result into the backend container, and loads it
with scripts/seed_baseline.py: a dry run first, then the real load. Afterwards the public
catalogue at /api/v1/catalogue/entries lists the seeded entries.

This is for development and evaluation only. It is not part of 'docker compose up', and a
production baseline is never filtered. See docs/operations/runbooks/seed-baseline.md.

Needs: uv on PATH, Docker, and a running stack ('docker compose -f deploy/compose.yml up -d
--build') whose 'migrate' service has finished.

Exit codes: 0 seeded; 4 the catalogue already holds data, nothing written; any other non-zero
value is the failing step's own exit code (the loader's codes are listed in the runbook).

.EXAMPLE
powershell -File scripts/dev-seed.ps1
#>
[CmdletBinding()]
param(
    [string]$ReleaseName = '2026-06'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$ComposeFile = Join-Path $RepoRoot 'deploy\compose.yml'
$Workbook = Join-Path $RepoRoot 'transform\tests\fixtures\spia-requesting-sample.xlsx'
$WorkDir = Join-Path ([System.IO.Path]::GetTempPath()) ('nptc-dev-seed-' + [guid]::NewGuid().ToString('N'))
$ContainerPath = '/tmp/import-dataset.json'
$ExitCatalogueNotEmpty = 4

function Invoke-Step {
    param([string]$Title, [string]$File, [string[]]$Arguments)
    Write-Host "==> $Title"
    & $File @Arguments | Out-Host
    return $LASTEXITCODE
}

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host 'error: uv is not on PATH. Install it or add it to PATH, then run this script again.'
    exit 1
}
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Host 'error: docker is not on PATH.'
    exit 1
}

Push-Location $RepoRoot
try {
    New-Item -ItemType Directory -Path $WorkDir | Out-Null
    $RawDir = Join-Path $WorkDir 'raw'
    $Filtered = Join-Path $WorkDir 'import-dataset.json'

    $running = & docker compose -f $ComposeFile ps --status running --services
    if (-not ($running -contains 'backend')) {
        Write-Host 'error: the backend service is not running. Start the stack first:'
        Write-Host '  docker compose -f deploy/compose.yml up -d --build'
        exit 1
    }

    $code = Invoke-Step 'Run the transform on the sample workbook' 'uv' @(
        'run', 'nptc-transform', 'run', '--workbook', $Workbook,
        '--emit-dataset', '--release-name', $ReleaseName, '--report-dir', $RawDir)
    if ($code -ne 0) { exit $code }

    $code = Invoke-Step 'Drop the entries the loader would refuse' 'uv' @(
        'run', 'python', 'scripts/dev_seed_filter.py',
        '--input', (Join-Path $RawDir 'import-dataset.json'), '--output', $Filtered)
    if ($code -ne 0) { exit $code }

    $code = Invoke-Step 'Copy the dataset into the backend container' 'docker' @(
        'compose', '-f', $ComposeFile, 'cp', $Filtered, "backend:$ContainerPath")
    if ($code -ne 0) { exit $code }

    $seedArguments = @('compose', '-f', $ComposeFile, 'exec', '-T', 'backend',
        'python', 'scripts/seed_baseline.py', '--dataset', $ContainerPath)

    $code = Invoke-Step 'Dry run (nothing is kept)' 'docker' ($seedArguments + '--dry-run')
    if ($code -eq $ExitCatalogueNotEmpty) {
        Write-Host 'The catalogue already holds data, so nothing was written.'
        Write-Host 'To seed again, reset the database: docker compose -f deploy/compose.yml down -v'
        exit $ExitCatalogueNotEmpty
    }
    if ($code -ne 0) { exit $code }

    $code = Invoke-Step 'Load the seed' 'docker' $seedArguments
    if ($code -ne 0) { exit $code }

    Write-Host 'Done. The seeded entries are listed at /api/v1/catalogue/entries.'
    exit 0
}
finally {
    Pop-Location
    if (Test-Path $WorkDir) { Remove-Item -Recurse -Force $WorkDir }
}
