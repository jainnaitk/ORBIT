param(
  [Parameter(Mandatory = $true)]
  [ValidatePattern('^\d+\.\d+\.\d+$')]
  [string]$Version
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

Write-Host "=== 1. Running backend tests & linter ===" -ForegroundColor Cyan
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check src tests

Write-Host "=== 2. Running frontend TypeScript check ===" -ForegroundColor Cyan
Push-Location "$Root\ui"
npx tsc --noEmit
Pop-Location

Write-Host "=== 3. Bumping version to $Version across all manifests ===" -ForegroundColor Cyan
(Get-Content "config.yaml" -Raw) -replace '(current_version:\s*")[^"]+(")', "`${1}$Version`${2}" | Set-Content "config.yaml" -NoNewline
(Get-Content "src\metrolite\config.py" -Raw) -replace '(current_version:\s*str\s*=\s*")[^"]+(")', "`${1}$Version`${2}" | Set-Content "src\metrolite\config.py" -NoNewline
(Get-Content "pyproject.toml" -Raw) -replace '(?m)^(version\s*=\s*")[^"]+(")', "`${1}$Version`${2}" | Set-Content "pyproject.toml" -NoNewline
(Get-Content "ui\package.json" -Raw) -replace '("version"\s*:\s*")[^"]+(")', "`${1}$Version`${2}" | Set-Content "ui\package.json" -NoNewline
(Get-Content "ui\src-tauri\tauri.conf.json" -Raw) -replace '("version"\s*:\s*")[^"]+(")', "`${1}$Version`${2}" | Set-Content "ui\src-tauri\tauri.conf.json" -NoNewline
(Get-Content "ui\src-tauri\Cargo.toml" -Raw) -replace '(?m)^(version\s*=\s*")[^"]+(")', "`${1}$Version`${2}" | Set-Content "ui\src-tauri\Cargo.toml" -NoNewline

$changelogPath = Join-Path $Root "CHANGELOG.md"
if (Test-Path $changelogPath) {
  $changelogRaw = Get-Content $changelogPath -Raw
  if ($changelogRaw -notmatch "(?m)^##\s+v?$([regex]::Escape($Version))\b") {
    $prevTag = (& git describe --tags --abbrev=0 --match "v*" 2>$null | Select-Object -First 1)
    $logRange = if ($prevTag) { "$prevTag..HEAD" } else { "-n", "10" }
    $commits = @(& git log $logRange --pretty=format:"%s" 2>$null | Where-Object { $_ -and $_ -notmatch '^(Release v|Merge )' })
    $bullets = New-Object System.Collections.Generic.List[string]
    foreach ($c in $commits) {
      $clean = ($c -replace '^(feat|fix|chore|refactor|perf|docs)(\([^)]+\))?:\s*', '').Trim()
      if ($clean.Length -ge 4 -and -not $bullets.Contains("- $clean")) {
        $bullets.Add("- $clean")
      }
      if ($bullets.Count -ge 4) { break }
    }
    if ($bullets.Count -lt 2) {
      if ($bullets.Count -eq 0) { $bullets.Add("- Updated ORBIT desktop app and audio engine") }
      $bullets.Add("- Performance, stability, and updater improvements")
    }
    $newEntry = "## v$Version`r`n" + ($bullets -join "`r`n") + "`r`n`r`n"
    $updatedChangelog = $changelogRaw -replace '(?m)^(# ORBIT Changelog\r?\n\r?\n)', "`$1$newEntry"
    if ($updatedChangelog -eq $changelogRaw) {
      $updatedChangelog = "# ORBIT Changelog`r`n`r`n$newEntry" + $changelogRaw
    }
    Set-Content -Path $changelogPath -Value $updatedChangelog -NoNewline -Encoding UTF8
    Write-Host "Added v$Version release bullets to CHANGELOG.md" -ForegroundColor Green
  }
}

Write-Host "=== 4. Committing, tagging v$Version & pushing to GitHub ===" -ForegroundColor Cyan
$stageFiles = @("config.yaml", "src/metrolite/config.py", "pyproject.toml", "ui/package.json", "ui/src-tauri/tauri.conf.json", "ui/src-tauri/Cargo.toml")
if (Test-Path $changelogPath) { $stageFiles += "CHANGELOG.md" }
git add @stageFiles
git commit -m "Release v$Version"
git tag "v$Version"
git push origin main --tags

Write-Host "Done! GitHub Actions is now building ORBIT v$Version and publishing the Release." -ForegroundColor Green
