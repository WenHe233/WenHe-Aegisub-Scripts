param(
    [Parameter(Mandatory=$true)][ValidatePattern('^\d+\.\d+\.\d+$')][string]$Version,
    [string]$SessionDir,
    [string]$OfflineBundle,
    [string]$CacheRoot = (Join-Path $env:LOCALAPPDATA 'WenHe\AegisubScripts\ASSTracker\versions'),
    [switch]$PrepareOnly
)
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
Add-Type -AssemblyName System.IO.Compression.FileSystem

function Get-SHA256([string]$Path) {
    $stream = [IO.File]::OpenRead($Path)
    $hash = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($hash.ComputeHash($stream))).Replace('-','').ToLowerInvariant() }
    finally { $stream.Dispose(); $hash.Dispose() }
}

function Assert-ChildPath([string]$Base, [string]$Relative) {
    $parent = [IO.Path]::GetFullPath($Base).TrimEnd('\','/') + [IO.Path]::DirectorySeparatorChar
    $target = [IO.Path]::GetFullPath((Join-Path $Base $Relative))
    if (-not $target.StartsWith($parent, [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe package path.' }
    return $target
}
function Test-Runtime([string]$Directory) {
    $manifestPath = Join-Path $Directory 'runtime.json'
    if (-not (Test-Path -LiteralPath $manifestPath)) { throw 'Runtime manifest is missing.' }
    $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($manifest.schema -ne 1 -or $manifest.version -ne $Version -or $manifest.platform -ne 'Windows-x64') {
        throw 'Runtime version or platform mismatch.'
    }
    foreach ($required in @('ASSTracker.exe','ffmpeg/ffmpeg.exe','ffmpeg/ffprobe.exe','_internal/ass_tracker/VERSION')) {
        if (-not $manifest.files.PSObject.Properties[$required]) { throw "Missing required file: $required" }
    }
    foreach ($entry in $manifest.files.PSObject.Properties) {
        $path = Assert-ChildPath $Directory $entry.Name
        if ($entry.Value -notmatch '^[a-f0-9]{64}$' -or -not (Test-Path -LiteralPath $path -PathType Leaf)) { throw 'Incomplete runtime.' }
        if ((Get-SHA256 $path) -ne $entry.Value) { throw "Runtime checksum mismatch: $($entry.Name)" }
    }
    $embeddedVersion = [IO.File]::ReadAllText((Join-Path $Directory '_internal/ass_tracker/VERSION')).Trim()
    if ($embeddedVersion -ne $Version) { throw 'Embedded runtime version mismatch.' }
    return $manifest
}

$stage = $null
try {
    if (-not [Environment]::Is64BitOperatingSystem) { throw 'Windows x64 is required.' }
    $CacheRoot = [IO.Path]::GetFullPath($CacheRoot)
    New-Item -ItemType Directory -Force -Path $CacheRoot | Out-Null
    $destination = Assert-ChildPath $CacheRoot $Version
    if (-not (Test-Path -LiteralPath $destination)) {
        $stageName = '.staging-' + [guid]::NewGuid().ToString('N')
        $stage = Assert-ChildPath $CacheRoot $stageName
        New-Item -ItemType Directory -Path $stage | Out-Null
        $archive = Join-Path $stage 'package.zip'
        if ($OfflineBundle) {
            $bundle = [IO.Path]::GetFullPath($OfflineBundle)
            Test-Runtime $bundle | Out-Null
            $unpacked = Join-Path $stage 'unpacked'
            New-Item -ItemType Directory -Path $unpacked | Out-Null
            Get-ChildItem -LiteralPath $bundle -Force | Copy-Item -Destination $unpacked -Recurse
        } else {
            $base = "https://github.com/WenHe233/WenHe-Aegisub-Scripts/releases/download/wenhe.ASSTracker-v$Version"
            $release = Invoke-RestMethod -Uri "$base/release-manifest.json" -TimeoutSec 60
            $asset = "wenhe.ASSTracker-$Version-Windows-x64.zip"
            if ($release.schema -ne 1 -or $release.version -ne $Version -or $release.platform -ne 'Windows-x64' -or
                $release.asset -ne $asset -or $release.sha256 -notmatch '^[a-f0-9]{64}$') { throw 'Invalid release manifest.' }
            Invoke-WebRequest -UseBasicParsing -Uri "$base/$asset" -OutFile $archive -TimeoutSec 900
            if ((Get-Item -LiteralPath $archive).Length -ne $release.size -or
                (Get-SHA256 $archive) -ne $release.sha256) { throw 'Download checksum mismatch.' }
            $unpacked = Join-Path $stage 'unpacked'
            $zip = [IO.Compression.ZipFile]::OpenRead($archive)
            try {
                foreach ($entry in $zip.Entries) { Assert-ChildPath $unpacked $entry.FullName | Out-Null }
            } finally { $zip.Dispose() }
            [IO.Compression.ZipFile]::ExtractToDirectory($archive, $unpacked)
        }
        Test-Runtime $unpacked | Out-Null
        # Destination is a validated version child; do not overwrite an existing install.
        if (Test-Path -LiteralPath $destination) { Test-Runtime $destination | Out-Null }
        else { Move-Item -LiteralPath $unpacked -Destination $destination }
    }
    Test-Runtime $destination | Out-Null
    if (-not $PrepareOnly) {
        if (-not $SessionDir) { throw 'Session directory is required.' }
        $job = Get-Content -LiteralPath (Join-Path $SessionDir 'job.json') -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($job.tool_version -and $job.tool_version -ne $Version) { throw 'Job version mismatch.' }
        # Native invocation passes a literal argument array; no shell command construction.
        & (Join-Path $destination 'ASSTracker.exe') --bridge-dir $SessionDir
        if ($LASTEXITCODE -ne 0) { throw "Tracking window exited with code $LASTEXITCODE." }
    }
    Write-Output $destination
} catch {
    if ($SessionDir -and (Test-Path -LiteralPath $SessionDir -PathType Container)) {
        [IO.File]::AppendAllText((Join-Path $SessionDir 'error.log'), ($_ | Out-String), [Text.UTF8Encoding]::new($false))
    }
    Write-Error $_
    exit 1
} finally {
    if ($stage -and (Test-Path -LiteralPath $stage)) {
        $verified = Assert-ChildPath $CacheRoot ([IO.Path]::GetFileName($stage))
        if ($verified -eq $stage -and [IO.Path]::GetFileName($stage) -match '^\.staging-[a-f0-9]{32}$') {
            Remove-Item -LiteralPath $stage -Recurse -Force
        }
    }
}
