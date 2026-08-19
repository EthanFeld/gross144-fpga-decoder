param(
    [Parameter(Mandatory = $true)] [string]$Port,
    [ValidateSet('X', 'Z')] [string]$Basis = 'X',
    [int]$Shots = 300000,
    [string]$Bitstream,
    [string]$DeferredCorpus,
    [string]$Output,
    [switch]$Smoke,
    [switch]$FastFirst
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$clockConfig = Get-Content -Raw -LiteralPath (Join-Path $root 'config\board_clock.json') | ConvertFrom-Json
$coreClockHz = [int64]$clockConfig.core_clock_hz
if ($Shots -lt 1) { throw 'Shots must be positive' }
if ([string]::IsNullOrWhiteSpace($Output)) {
    $Output = Join-Path $root "build\board_proof_$($Basis.ToLower())_$Shots.json"
} elseif (-not [IO.Path]::IsPathRooted($Output)) {
    $Output = Join-Path $root $Output
}
if ([string]::IsNullOrWhiteSpace($Bitstream)) {
    $project = "paper_gross144_s1w_four_lane_uart_production_$($Basis.ToLower())"
    $bitstreamFile = Get-ChildItem -LiteralPath (Join-Path $root "build\$project") `
        -Recurse -Filter "$project.fs" -File | Select-Object -First 1
    if ($null -eq $bitstreamFile) { throw "No production bitstream. Run tools/build_board.ps1 -Basis $Basis" }
    # Force a plain string. Passing a FileInfo through a native-style Python
    # argument array can collapse to an empty value on Windows PowerShell.
    $Bitstream = $bitstreamFile.FullName.ToString()
} elseif (-not [IO.Path]::IsPathRooted($Bitstream)) {
    $Bitstream = Join-Path $root $Bitstream
}

$arguments = @(
    'tools/run_paper_gross144_four_lane_board_campaign.py',
    '--port', $Port, '--basis', $Basis, '--shots', "$Shots",
    '--baud', [string]$clockConfig.uart_baud, '--core-clock-hz', [string]$coreClockHz,
    '--bitstream', $Bitstream, '--cpu-telescope-handoff',
    '--cpu-backend', 'c', '--output', $Output
)
if (-not [string]::IsNullOrWhiteSpace($DeferredCorpus)) {
    if (-not [IO.Path]::IsPathRooted($DeferredCorpus)) {
        $DeferredCorpus = Join-Path $root $DeferredCorpus
    }
    $arguments += @('--deferred-corpus', $DeferredCorpus)
}
if ($FastFirst) { $arguments += '--fast-first' }
if ($Smoke) { $arguments += '--smoke' }
Push-Location $root
try {
    & python @arguments
    if ($LASTEXITCODE -ne 0) { throw "Board proof failed with exit code $LASTEXITCODE" }
} finally {
    Pop-Location
}
