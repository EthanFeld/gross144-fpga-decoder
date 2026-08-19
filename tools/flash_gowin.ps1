param(
    [ValidateSet('sram', 'flash')]
    [string]$Mode = 'sram',
    [ValidateSet('X', 'Z')]
    [string]$Basis = 'X',
    [string]$GowinHome = $env:GOWIN_HOME,
    [int]$Channel = 0,
    [string]$Bitstream
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if ([string]::IsNullOrWhiteSpace($GowinHome)) {
    $GowinHome = 'C:\Gowin\Gowin_V1.9.11.03_Education_x64'
}
$programmer = Join-Path $GowinHome 'Programmer\bin\programmer_cli.exe'
if (-not (Test-Path -LiteralPath $programmer -PathType Leaf)) {
    throw "Gowin programmer_cli not found: $programmer"
}

$fs = $null
if (-not [string]::IsNullOrWhiteSpace($Bitstream)) {
    $candidate = if ([IO.Path]::IsPathRooted($Bitstream)) { $Bitstream } else { Join-Path $root $Bitstream }
    if (Test-Path -LiteralPath $candidate -PathType Leaf) {
        $fs = Get-Item -LiteralPath $candidate
    } else {
        throw "Gowin bitstream not found: $candidate"
    }
} else {
    $project = "paper_gross144_s1w_four_lane_uart_production_$($Basis.ToLower())"
    $expected = Join-Path $root "build\$project\$project\impl\pnr\$project.fs"
    if (Test-Path -LiteralPath $expected -PathType Leaf) {
        $fs = Get-Item -LiteralPath $expected
    }
}
if ($null -eq $fs) {
    throw 'No Gowin .fs bitstream. Run the Gowin build first or pass -Bitstream.'
}

# Gowin operation 2 = SRAM program; 9 = external flash erase/program/verify.
$operation = if ($Mode -eq 'sram') { 2 } else { 9 }
$report = Join-Path $root 'build\gowin\programmer.txt'
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $report) | Out-Null
$args = @(
    '--device', 'GW2AR-18C',
    '--operation_index', "$operation",
    '--fsFile', $fs.FullName,
    '--cable-index', '1',
    '--channel', "$Channel",
    '--output', $report
)

Write-Host "Gowin program: mode=$Mode bitstream=$($fs.FullName)"
& $programmer @args
if ($LASTEXITCODE -ne 0) {
    throw "Gowin programming failed with exit code $LASTEXITCODE"
}
