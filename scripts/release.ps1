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

Write-Host "=== 4. Committing, tagging v$Version & pushing to GitHub ===" -ForegroundColor Cyan
git add config.yaml src/metrolite/config.py pyproject.toml ui/package.json ui/src-tauri/tauri.conf.json ui/src-tauri/Cargo.toml
git commit -m "Release v$Version"
git tag "v$Version"
git push origin main --tags

Write-Host "Done! GitHub Actions is now building ORBIT v$Version and publishing the Release." -ForegroundColor Green
