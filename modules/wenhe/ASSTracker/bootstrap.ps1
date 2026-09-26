param(
    [Parameter(Mandatory=$true)][ValidatePattern('^\d+\.\d+\.\d+$')][string]$Version,
    [string]$SessionDir,
    [string]$OfflineBundle,
    [string]$CacheRoot = (Join-Path $env:LOCALAPPDATA 'WenHe\AegisubScripts\ASSTracker\versions'),
    [string]$Mirror = '',
    [string]$ReleaseBaseUrl = 'https://github.com/WenHe233/WenHe-Aegisub-Scripts/releases/download',
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

# Session files for the Aegisub macro: one progress line, and a cancel request.
function Write-Stage([string]$Text) {
    if (-not $SessionDir) { return }
    try { [IO.File]::WriteAllText((Join-Path $SessionDir 'progress'), $Text, [Text.UTF8Encoding]::new($false)) } catch { }
}
function Test-Cancelled {
    return [bool]($SessionDir -and [IO.File]::Exists((Join-Path $SessionDir 'cancel')))
}

function Open-Response([string]$Url, [int]$ConnectSeconds, [int]$ReadSeconds) {
    $request = [Net.HttpWebRequest]::Create($Url)
    $request.Timeout = $ConnectSeconds * 1000
    $request.ReadWriteTimeout = $ReadSeconds * 1000
    $request.UserAgent = "WenHe-ASSTracker/$Version"
    return $request.GetResponse()
}
function Get-Json([string]$Url, [int]$TimeoutSeconds) {
    # Read bytes and parse locally; mirrors do not agree on the Content-Type.
    $response = Open-Response $Url $TimeoutSeconds $TimeoutSeconds
    try {
        $stream = $response.GetResponseStream()
        $memory = New-Object IO.MemoryStream
        $buffer = New-Object byte[] 65536
        while (($read = $stream.Read($buffer, 0, $buffer.Length)) -gt 0) {
            if ($memory.Length + $read -gt 65536) { throw 'Release manifest is too large.' }
            $memory.Write($buffer, 0, $read)
        }
        return [Text.Encoding]::UTF8.GetString($memory.ToArray()).TrimStart([char]0xFEFF) | ConvertFrom-Json
    } finally { $response.Close() }
}
function Save-Download([string]$Url, [string]$Path, [long]$Size, [string]$ManifestSource) {
    $response = Open-Response $Url 30 60
    try {
        if ($response.ContentLength -ge 0 -and $response.ContentLength -ne $Size) { throw 'Download size differs from the release manifest.' }
        $stream = $response.GetResponseStream()
        $file = [IO.File]::Create($Path)
        try {
            $buffer = New-Object byte[] 1048576
            $done = [long]0
            $clock = [Diagnostics.Stopwatch]::StartNew()
            Write-Stage "download 0 $Size $ManifestSource"
            while (($read = $stream.Read($buffer, 0, $buffer.Length)) -gt 0) {
                $done += $read
                if ($done -gt $Size) { throw 'Download is larger than the release manifest.' }
                $file.Write($buffer, 0, $read)
                if ($clock.ElapsedMilliseconds -ge 250) {
                    $clock.Restart()
                    if (Test-Cancelled) { throw [OperationCanceledException]::new('Download cancelled.') }
                    Write-Stage "download $done $Size $ManifestSource"
                }
            }
            Write-Stage "download $done $Size $ManifestSource"
        } finally { $file.Dispose() }
    } finally { $response.Close() }
}

$stage = $null
$phase = 'prepare'
try {
    if (-not [Environment]::Is64BitOperatingSystem) { throw 'Windows x64 is required.' }
    # HTTPS only; plain HTTP is accepted solely on loopback for local tests.
    $origin = '(https://[A-Za-z0-9.-]+(:\d{1,5})?|http://127\.0\.0\.1:\d{1,5})(/[A-Za-z0-9._~%-]+)*'
    if ($Mirror -and $Mirror -notmatch "^$origin/$") { throw 'Invalid mirror prefix.' }
    if ($ReleaseBaseUrl -notmatch "^$origin$") { throw 'Invalid release URL.' }
    $CacheRoot = [IO.Path]::GetFullPath($CacheRoot)
    New-Item -ItemType Directory -Force -Path $CacheRoot | Out-Null
    $destination = Assert-ChildPath $CacheRoot $Version
    if (-not (Test-Path -LiteralPath $destination)) {
        # Remove partial downloads left by a crash or a killed process tree.
        foreach ($old in Get-ChildItem -LiteralPath $CacheRoot -Directory -Force) {
            if ($old.Name -match '^\.staging-[a-f0-9]{32}$' -and $old.LastWriteTimeUtc -lt [DateTime]::UtcNow.AddHours(-24)) {
                try { Remove-Item -LiteralPath $old.FullName -Recurse -Force } catch { }
            }
        }
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
            $official = "$ReleaseBaseUrl/wenhe.ASSTracker-v$Version"
            Write-Stage 'manifest'
            # The manifest carries the trusted SHA-256. Prefer GitHub directly even
            # when the large archive comes through a third-party mirror.
            $manifestSource = 'github'
            if ($Mirror) {
                try { $release = Get-Json "$official/release-manifest.json" 15 }
                catch {
                    $manifestSource = 'mirror'
                    $release = Get-Json "$Mirror$official/release-manifest.json" 60
                }
            } else { $release = Get-Json "$official/release-manifest.json" 60 }
            $asset = "wenhe.ASSTracker-$Version-Windows-x64.zip"
            $size = $release.size
            if ($release.schema -ne 1 -or $release.version -ne $Version -or $release.platform -ne 'Windows-x64' -or
                $release.asset -ne $asset -or $release.sha256 -notmatch '^[a-f0-9]{64}$' -or
                -not ($size -is [int] -or $size -is [long]) -or $size -le 0 -or $size -gt 4GB) { throw 'Invalid release manifest.' }
            Save-Download "$Mirror$official/$asset" $archive $size $manifestSource
            Write-Stage 'verify'
            if ((Get-Item -LiteralPath $archive).Length -ne $size -or
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
    $phase = 'launch'
    if (-not $PrepareOnly) {
        if (-not $SessionDir) { throw 'Session directory is required.' }
        Write-Stage 'launch'
        $job = Get-Content -LiteralPath (Join-Path $SessionDir 'job.json') -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($job.tool_version -and $job.tool_version -ne $Version) { throw 'Job version mismatch.' }
        # Windows PowerShell does not wait for a GUI-subsystem executable invoked
        # with &. Wait explicitly so Aegisub can receive the result.
        # Quote one Windows argv value; no command shell is involved.
        $argument = [regex]::Replace([IO.Path]::GetFullPath($SessionDir), '(\\*)"', '$1$1\"')
        $argument = [regex]::Replace($argument, '(\\+)$', '$1$1')
        $start = New-Object Diagnostics.ProcessStartInfo
        $start.FileName = Join-Path $destination 'ASSTracker.exe'
        $start.Arguments = '--bridge-dir "' + $argument + '"'
        $start.UseShellExecute = $false
        $start.CreateNoWindow = $true
        $process = [Diagnostics.Process]::Start($start)
        try { $process.WaitForExit(); $exitCode = $process.ExitCode }
        finally { $process.Dispose() }
        if ($exitCode -ne 0) { throw "Tracking window exited with code $exitCode." }
    }
    Write-Output $destination
} catch {
    # Exit codes: 1 tracking window failed, 3 runtime download/verification failed, 4 cancelled.
    if ($_.Exception -is [OperationCanceledException]) { exit 4 }
    if ($SessionDir -and (Test-Path -LiteralPath $SessionDir -PathType Container)) {
        [IO.File]::AppendAllText((Join-Path $SessionDir 'error.log'), ($_ | Out-String), [Text.UTF8Encoding]::new($false))
    }
    Write-Error $_ -ErrorAction Continue
    if ($phase -eq 'prepare') { exit 3 }
    exit 1
} finally {
    if ($stage -and (Test-Path -LiteralPath $stage)) {
        $verified = Assert-ChildPath $CacheRoot ([IO.Path]::GetFileName($stage))
        if ($verified -eq $stage -and [IO.Path]::GetFileName($stage) -match '^\.staging-[a-f0-9]{32}$') {
            Remove-Item -LiteralPath $stage -Recurse -Force
        }
    }
}
