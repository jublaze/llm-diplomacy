#!/usr/bin/env pwsh
param(
    [Parameter()]
    [string]$Root
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = if ([string]::IsNullOrWhiteSpace($Root)) {
    (Resolve-Path -LiteralPath (Split-Path -Parent $PSScriptRoot)).Path
} else {
    (Resolve-Path -LiteralPath $Root -ErrorAction Stop).Path
}
if (-not (Test-Path -LiteralPath $repoRoot -PathType Container)) {
    throw 'Scan root must be an existing directory.'
}

$excludeDirs = @(
    '^\.git($|\\)',
    '^\.venv($|\\)',
    '^\.pytest_cache($|\\)',
    '(^|\\)__pycache__($|\\)',
    '(^|\\)node_modules($|\\)',
    '(^|\\)(dist|dist-public|build|coverage|test-results|public-build-input|public-build-audits)($|\\)',
    '(^|\\)\.aws($|\\)',
    '^experiments($|\\)',
    '^\.codex($|\\)',
    '^\.agents($|\\)'
)

$patterns = @(
    @{ Name = 'OpenAI API key'; Regex = 'sk-[A-Za-z0-9]{20,}' },
    @{ Name = 'AWS access key id'; Regex = 'AKIA[0-9A-Z]{16}' },
    @{ Name = 'Generic private key block'; Regex = '-----BEGIN (RSA|EC|OPENSSH|DSA|PRIVATE) KEY-----' },
    @{ Name = 'Env assignment likely secret'; Regex = '(?i)\b(api[_-]?key|secret|token|password)\b\s*[:=]\s*[A-Za-z0-9_\-]{12,}' },
    @{ Name = 'Bearer token'; Regex = '(?i)bearer\s+[A-Za-z0-9\-\._~\+\/]+=*' }
)

$files = Get-ChildItem -LiteralPath $repoRoot -Recurse -File -Force -ErrorAction SilentlyContinue | Where-Object {
    $rel = $_.FullName.Substring($repoRoot.Length + 1)
    $rel -ne 'scripts\security_scan.ps1' -and $_.Name -ne 'security_scan.ps1' -and
    -not ($excludeDirs | ForEach-Object { $rel -match $_ } | Where-Object { $_ })
}

$findings = @()
foreach ($file in $files) {
    $rel = $file.FullName.Substring($repoRoot.Length + 1)
    try {
        $lines = @(Get-Content -LiteralPath $file.FullName -ErrorAction Stop)
    } catch {
        continue
    }
    for ($i = 0; $i -lt $lines.Count; $i++) {
        $line = [string]$lines[$i]
        foreach ($pat in $patterns) {
            if ($line -match $pat.Regex) {
                $findings += [PSCustomObject]@{
                    File = $rel
                    Line = ($i + 1)
                    Pattern = $pat.Name
                }
            }
        }
    }
}

if ($findings.Count -eq 0) {
    Write-Host 'Security scan passed: no obvious secret patterns found.' -ForegroundColor Green
    exit 0
}

Write-Host ("Security scan found {0} potential issue(s):" -f $findings.Count) -ForegroundColor Yellow
$findings | ForEach-Object {
    Write-Host ("- {0}:{1} [{2}] potential secret value redacted" -f $_.File, $_.Line, $_.Pattern)
}

exit 1
