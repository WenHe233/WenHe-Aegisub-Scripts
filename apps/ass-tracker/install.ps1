param([string]$AutomationRoot = (Join-Path $env:APPDATA 'Aegisub\automation'))
$ErrorActionPreference = 'Stop'
$bundle = $PSScriptRoot
$manifest = Get-Content -LiteralPath (Join-Path $bundle 'runtime.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$module = Join-Path $bundle 'automation\include\wenhe\ASSTracker'
& (Join-Path $module 'bootstrap.ps1') -Version $manifest.version -OfflineBundle $bundle -PrepareOnly
if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) { throw 'Runtime setup failed.' }
$AutomationRoot = [IO.Path]::GetFullPath($AutomationRoot)
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
$backup = Join-Path $AutomationRoot ('backups\ASSTracker-' + $stamp)
foreach ($part in @('autoload','include')) {
    $source = Join-Path $bundle ('automation\' + $part)
    foreach ($file in Get-ChildItem -LiteralPath $source -File -Recurse) {
        $relative = $file.FullName.Substring($source.Length).TrimStart('\','/')
        $target = Join-Path (Join-Path $AutomationRoot $part) $relative
        if (Test-Path -LiteralPath $target) {
            $old = Join-Path (Join-Path $backup $part) $relative
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $old) | Out-Null
            Copy-Item -LiteralPath $target -Destination $old
        }
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
        Copy-Item -LiteralPath $file.FullName -Destination $target -Force
    }
}
$legacy = Join-Path $AutomationRoot 'autoload\ass_tracker.lua'
if (Test-Path -LiteralPath $legacy) {
    New-Item -ItemType Directory -Force -Path $backup | Out-Null
    Move-Item -LiteralPath $legacy -Destination (Join-Path $backup 'ass_tracker.lua')
}
Write-Output 'Installed. Reload Automation scripts or restart Aegisub. Existing scripts were backed up.'
