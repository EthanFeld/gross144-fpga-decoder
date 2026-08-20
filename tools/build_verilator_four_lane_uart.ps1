param(
    [string]$Mdir = 'build\obj_four_lane_uart'
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$verilatorRoot = 'C:\oss-cad-suite\oss-cad-suite\share\verilator'
$verilator = 'C:\oss-cad-suite\oss-cad-suite\bin\verilator_bin.exe'
$make = 'C:\msys64\mingw64\bin\mingw32-make.exe'
$mingw = 'C:\msys64\mingw64\bin'
$env:VERILATOR_ROOT = $verilatorRoot
$env:PATH = "$mingw;" + $env:PATH

Push-Location $root
try {
    & $verilator --cc --main --timing `
        --top-module tb_tang_nano_20k_paper_s1w_four_lane_uart_top `
        -DGROSS144_SIM -Wno-fatal -f tools\rtl_s1w_four_lane_uart_top.f `
        --Mdir $Mdir -o four_lane_uart_sim.exe
    if ($LASTEXITCODE -ne 0) { throw "Verilator generation failed: $LASTEXITCODE" }

    $savedPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & $make -C $Mdir `
        -f Vtb_tang_nano_20k_paper_s1w_four_lane_uart_top.mk -j4 2>&1 | Out-Null
    $ErrorActionPreference = $savedPreference

    $include = Join-Path $verilatorRoot 'include'
    $savedPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & g++.exe -std=c++20 -O2 -I$Mdir -I$include -I(Join-Path $include 'vltstd') `
        -DVERILATOR=1 -DVM_TIMING=1 -DVL_TIME_CONTEXT -fcoroutines `
        -c (Join-Path $include 'verilated.cpp') -o (Join-Path $Mdir 'verilated2.o') `
        2>&1 | Out-Null
    $compileExit = $LASTEXITCODE
    $ErrorActionPreference = $savedPreference
    if ($compileExit -ne 0) { throw "Verilator runtime compile failed: $compileExit" }

    $modelObjects = Get-ChildItem -LiteralPath $Mdir `
        -Filter 'Vtb_tang_nano_20k_paper_s1w_four_lane_uart_top*.o' |
        ForEach-Object { $_.FullName }
    if (-not $modelObjects) { throw 'Generated Verilator model objects are missing' }
    & g++.exe @modelObjects `
        (Join-Path $Mdir 'verilated2.o') `
        (Join-Path $Mdir 'verilated_timing.o') `
        (Join-Path $Mdir 'verilated_threads.o') `
        -pthread -lpthread -latomic -o (Join-Path $Mdir 'four_lane_uart_sim.exe')
    if ($LASTEXITCODE -ne 0) { throw "Verilator model link failed: $LASTEXITCODE" }
    Write-Output (Resolve-Path (Join-Path $Mdir 'four_lane_uart_sim.exe')).Path
} finally {
    Pop-Location
}
