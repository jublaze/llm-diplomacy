#!/usr/bin/env pwsh
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$hookPath = Join-Path $repoRoot '.git\hooks\pre-commit'
$scanScript = Join-Path $repoRoot 'scripts\security_scan.ps1'

if (-not (Test-Path (Join-Path $repoRoot '.git'))) {
    throw 'No .git directory found. Initialize git first.'
}

$hook = @"
#!/usr/bin/env pwsh
& '$scanScript'
if (`$LASTEXITCODE -ne 0) {
  Write-Host 'Pre-commit blocked: potential secrets detected.' -ForegroundColor Red
  exit 1
}
exit 0
"@

$hook | Set-Content -LiteralPath $hookPath
Write-Host "Installed pre-commit hook at $hookPath" -ForegroundColor Green
