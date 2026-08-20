param(
    [ValidateSet('X', 'Z')]
    [string]$Basis = 'X',
    [string]$GowinHome = $env:GOWIN_HOME
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$clockConfig = Get-Content -Raw -LiteralPath (Join-Path $root 'config\board_clock.json') | ConvertFrom-Json
$requestedClockHz = [double]$clockConfig.core_clock_hz
$requestedPeriodNs = 1.0e9 / $requestedClockHz

function Hash-Text([string]$text) {
    $bytes = [Text.Encoding]::UTF8.GetBytes($text)
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        return ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace('-', '').ToLowerInvariant()
    } finally {
        $sha.Dispose()
    }
}

function Hash-Tree([string]$path) {
    if (-not (Test-Path -LiteralPath $path -PathType Container)) { return 'missing' }
    $records = Get-ChildItem -LiteralPath $path -Recurse -File | Sort-Object FullName |
        ForEach-Object { "$($_.FullName.Substring($path.Length).Replace('\','/'))=$((Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant())" }
    return Hash-Text ($records -join "`n")
}
if ([string]::IsNullOrWhiteSpace($GowinHome)) {
    $GowinHome = 'C:\Gowin\Gowin_V1.9.11.03_Education_x64'
}
$gwSh = Join-Path $GowinHome 'IDE\bin\gw_sh.exe'
if (-not (Test-Path -LiteralPath $gwSh -PathType Leaf)) {
    throw "Gowin gw_sh not found: $gwSh"
}

$project = "paper_gross144_s1w_four_lane_uart_production_$($Basis.ToLower())"
$projectRoot = Join-Path $root "build\$project"
$imageSource = Join-Path $root "artifacts\paper_gross144_s1_templates_p002\paper_gross144_s1_$Basis.json"
$imageOutput = Join-Path $root "build\generated\paper_gross144_s1w_$($Basis.ToLower())_p002"
$imageProvenance = Join-Path $imageOutput 'provenance.json'
if (-not (Test-Path -LiteralPath $imageSource -PathType Leaf)) {
    throw "Frozen Paper Gross144 image missing: $imageSource"
}
$generatorInputs = @(
    "basis=$Basis",
    "p=0.002",
    "clock_hz=$requestedClockHz",
    "source_image=$((Get-FileHash -LiteralPath $imageSource -Algorithm SHA256).Hash.ToLowerInvariant())"
)
foreach ($dependency in @(
    'tools\export_paper_gross144_s1w_sv_image.py',
    'python\gross144_decoder\paper_gross144.py',
    'python\gross144_decoder\paper_gross144_component_templates.py',
    'python\gross144_decoder\paper_gross144_hash.py',
    'python\gross144_decoder\wide_minsum.py'
)) {
    $dependencyPath = Join-Path $root $dependency
    $generatorInputs += "$dependency=$((Get-FileHash -LiteralPath $dependencyPath -Algorithm SHA256).Hash.ToLowerInvariant())"
}
$relayRoot = Join-Path $root 'build\relay'
$generatorInputs += "relay_root=$(Hash-Tree $relayRoot)"
$generatorHash = Hash-Text ($generatorInputs -join "`n")
$provenanceCurrent = $null
if (Test-Path -LiteralPath $imageProvenance -PathType Leaf) {
    try { $provenanceCurrent = Get-Content -Raw -LiteralPath $imageProvenance | ConvertFrom-Json } catch { $provenanceCurrent = $null }
}
$imageStale = -not (Test-Path -LiteralPath (Join-Path $imageOutput 'meta.memb') -PathType Leaf) -or
              $null -eq $provenanceCurrent -or
              $provenanceCurrent.generator_sha256 -ne $generatorHash
if ($imageStale) {
    New-Item -ItemType Directory -Path $imageOutput -Force | Out-Null
    $portableManifestPath = Join-Path $root 'images\manifest.json'
    $portableImageUsable = $Basis -eq 'X' -and
        (Test-Path -LiteralPath $portableManifestPath -PathType Leaf) -and
        ((Get-Content -Raw -LiteralPath $portableManifestPath | ConvertFrom-Json).source_image_sha256 -eq
         (Get-FileHash -LiteralPath $imageSource -Algorithm SHA256).Hash.ToLowerInvariant())
    if ($portableImageUsable) {
        Write-Output 'Using tracked X ROM image payload as reproducible build input.'
        Copy-Item -Path (Join-Path $root 'images\*') -Destination $imageOutput -Force
    } else {
        if (-not (Test-Path -LiteralPath $relayRoot -PathType Container)) {
            throw "Cannot regenerate $Basis ROMs: pinned Relay fixture missing at $relayRoot and tracked portable image is absent or stale. Restore the exact fixture before building; stale ROMs are rejected intentionally."
        }
        Push-Location $root
        try {
            & python tools\export_paper_gross144_s1w_sv_image.py `
                --image $imageSource --output-dir $imageOutput `
                --relay-root $relayRoot --p 0.002
            if ($LASTEXITCODE -ne 0) { throw "FPGA image export failed with exit code $LASTEXITCODE" }
        } finally {
            Pop-Location
        }
    }
    [ordered]@{
        schema = 'GROSS144-FPGA-IMAGE-PROVENANCE-V1'
        generator_sha256 = $generatorHash
        inputs = $generatorInputs
        basis = $Basis
        physical_error_rate = 0.002
    } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $imageProvenance -Encoding utf8
}
if (-not (Test-Path -LiteralPath (Join-Path $imageOutput 'meta.memb') -PathType Leaf)) {
    throw "Generated FPGA image missing meta.memb: $imageOutput"
}
if (-not (Test-Path -LiteralPath $imageProvenance -PathType Leaf)) {
    throw "Generated FPGA image provenance missing: $imageProvenance"
}
$env:GROSS144_BASIS = $Basis
Push-Location $root
try {
    & $gwSh (Join-Path $root 'tools\gowin_paper_gross144_s1w_four_lane_uart.tcl')
    if ($LASTEXITCODE -ne 0) {
        throw "Gowin production build failed with exit code $LASTEXITCODE"
    }
} finally {
    Pop-Location
}

$pnrRoot = Join-Path $projectRoot "$project\impl\pnr"
$fsPath = Join-Path $pnrRoot "$project.fs"
$reportPath = Join-Path $pnrRoot "$project.rpt.txt"
$timingPath = Join-Path $pnrRoot "$project.timing_paths"
foreach ($required in @($fsPath, $reportPath, $timingPath)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Production build missing required artifact: $required"
    }
}

function Find-SlackNs([string]$text, [string]$kind) {
    $lines = $text -split "`r?`n"
    $active = $false
    $slacks = @()
    foreach ($line in $lines) {
        $trimmed = $line.Trim()
        if ($trimmed -eq 'SETUP' -or $trimmed -eq 'HOLD') {
            $active = $trimmed -eq $kind.ToUpperInvariant()
            continue
        }
        # Gowin .timing_paths is compact numeric report: first value after
        # each SETUP/HOLD header is slack, followed by arrival/required time.
        if ($active -and $trimmed -match '^[-+]?(?:\d+(?:\.\d*)?|\.\d+)$') {
            $slacks += [double]$trimmed
            $active = $false
        }
    }
    if ($slacks.Count -eq 0) { return $null }
    return ($slacks | Measure-Object -Minimum).Minimum
}

$timingText = (Get-Content -Raw -LiteralPath $timingPath) + "`n" +
              (Get-Content -Raw -LiteralPath $reportPath)
$setupSlackNs = Find-SlackNs $timingText 'setup'
$holdSlackNs = Find-SlackNs $timingText 'hold'
if ($null -eq $setupSlackNs) {
    throw "Could not parse post-route setup slack from $timingPath"
}
$fmaxMHz = $null
$availablePeriodNs = $requestedPeriodNs - [double]$setupSlackNs
if ($availablePeriodNs -gt 0) { $fmaxMHz = 1000.0 / $availablePeriodNs }
Write-Output ('Requested clock:      {0:N3} MHz' -f ($requestedClockHz / 1.0e6))
Write-Output ('Achieved/post-route Fmax: {0}' -f ($(if ($null -eq $fmaxMHz) { 'unknown' } else { '{0:N3} MHz' -f $fmaxMHz })))
Write-Output ('Worst setup slack:    {0:+0.000;-0.000;0.000} ns' -f [double]$setupSlackNs)
if ($null -eq $holdSlackNs) {
    Write-Output 'Worst hold slack:     unavailable'
} else {
    Write-Output ('Worst hold slack:     {0:+0.000;-0.000;0.000} ns' -f [double]$holdSlackNs)
}
if ([double]$setupSlackNs -lt 0.0) {
    throw "Post-route setup timing failed: $setupSlackNs ns"
}
if ($null -ne $holdSlackNs -and [double]$holdSlackNs -lt 0.0) {
    throw "Post-route hold timing failed: $holdSlackNs ns"
}
Write-Output 'Timing status:        PASS'
$sha = (Get-FileHash -LiteralPath $fsPath -Algorithm SHA256).Hash
Write-Output "BITSTREAM_SHA256=$sha"
Write-Output $fsPath
