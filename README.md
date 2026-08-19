# MITTEN Gross144 board endpoint

MITTEN is a Tang Nano 20K (`GW2AR-18C`) implementation of the Paper Gross144
S1W decoder. The release path is deliberately narrow: a timing-clean 40.5 MHz FPGA common
case, 3 Mbaud UART transport, and a resident optimized C Relay-lite tail for
rare FPGA defers.

Supported images are basis `X` and basis `Z`. Historical B3, H03, probe,
alternate-clock, and Open975 paths are not release interfaces.

## Current headline numbers

Target: `p = 0.2%`, block LER `<= 1e-5`, mean endpoint latency `<= 1 ms`.

| Board capture | Shots | Endpoint failures | Point LER | One-sided 95% upper bound | FPGA mean | Endpoint mean |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| X post-flash | 20,000 | 0 | 0 | `1.4978e-4` | `996.89 us` | `3.488 ms` |
| X clean capture | 50,000 | 0 | 0 | `5.9913e-5` | `993.76 us` | `3.515 ms` |

The 50,000-shot run is functional evidence, not an LER pass: its statistical
upper bound is still above `1e-5`. Endpoint latency is dominated by the CPU
tail; the measured CPU-handoff mean was about `50.0 ms` in that capture.

Paper comparison numbers supplied for this target:

| Metric | Paper reference | Current board evidence |
| --- | ---: | ---: |
| Block LER | `1.9e-6` | `<= 5.9913e-5` upper bound at 50k shots |
| LER / syndrome-extraction round | `1.6e-7` | not established on board |
| LER / round / logical qubit | `1.3e-8` | not established on board |

The 300,000-shot proof remains open: long runs entered a crash/cascade, and
the earlier completed run had five audited logical mismatches. Do not present
the current sample as paper-scale LER proof.

## Build status

The table below records historical basis-specific builds. The current 40.5 MHz
build is intentionally blocked until the pinned Relay fixture is restored;
the build wrapper rejects stale ROM provenance.

| Basis | Logic | BSRAM | Bitstream SHA-256 |
| --- | ---: | ---: | --- |
| X | 19,735/20,736 (96%) | 39/46 (85%) | `52ED2718D6693816452AFF05FED3BAC02E32BC9BBBF0C4D4417887D91D7BC7A6` |
| Z | 19,434/20,736 (94%) | 39/46 (85%) | `7911D08258EB4E87C01A533999B3E3205FACBE0381B27D34062C712D931BAF25` |

Clock source of truth: [`config/board_clock.json`](config/board_clock.json).
The prior 51 MHz image had negative setup slack; current production target is
40.5 MHz, with build rejection on negative post-route setup/hold slack.

## Algorithmic compression and reduction history

The reductions below were reconstructed from the optimization work and frozen
image reports. `Production` means present in the current FPGA/host path;
`Model` means validated in software or an RTL/storage model but not yet a
board claim.

### 1. FPGA/host split removes the full tail from the FPGA

| Before | After | Reduction |
| --- | --- | --- |
| FPGA carries the complete rare-tail decoder state | FPGA runs common S1W; host retains the full 1,728-bit syndrome only for rare defers | Removes the full S2R message/posterior image from the FPGA common path |
| 1,728 detector bits sent as general transport | 936 basis-filtered detector bits packed into 117 UART bytes | 45.8% fewer detector bits; 8:1 bit packing |

This is the core fit-to-board decision. The host already owns the sampled
syndrome, so a defer does not require a second 1,728-bit transfer.

### 2. Paper S1 spatial quotient

The full Paper S1 image has 8,784 variables, 936 checks, and 30,672 edges.
Its `Z12 x Z6` translation action has order 72 and reduces the stored
variable topology to 122 variable orbits plus reusable translated templates.

| Representation | Size |
| --- | ---: |
| Explicit edge topology | 429,408 bits |
| Quotient/template topology | 9,694 bits |
| Reduction | **44.3x smaller** |

The current image stores 13 time templates, 468 banked lane words, and 122
orbit-color words, then reconstructs translated rows by rule. This replaces a
936-row neighbor/address image with a compiler-checked quotient.

Status: `Production`.

### 3. Static four-bank schedule and address compression

The compiler assigns each of 122 orbits a color and uses
`(orbit_color + x) mod 4` for physical banking. Translation only rotates bank
names, so runtime conflict search and a large variable-address mux are removed.

- Maximum S1 template degree: 35 edges.
- Four-bank schedule: at most 9 four-edge beats per check.
- Largest per-bank address space: `122 x 18 = 2,196` entries, instead of a
  general roughly 9k-entry variable table.
- Four native posterior banks and explicit dual-port routing replace broad
  dynamic address fanout.

Status: `Production`.

### 4. Compressed check state and paired lanes

Per-check edge history is replaced by `min1`, `min2`, `argmin`, sign bits,
and validity state. The current production record is narrow enough for the
three-BSRAM compressed-record plan. Two checks with disjoint component-variable
sets are paired and run through two lockstep four-bank engines, giving four
parallel lanes without duplicating the graph.

This changes the implementation from a general edge-message store to compact
check records plus four banked posterior stores. It also halves the paired
check-update time in the validated architecture.

Status: `Production`.

### 5. 32-bit residual hash to a 20-bit equivariant gate

The exact 32-bit residual hash is formed from complete translation-equivariant
rotation blocks. The FPGA keeps a 20-bit projection, removing 12 bits (37.5%)
from the resident hash state while preserving translation structure.

The 20-bit value is rejection-only, never an acceptance certificate. Exact
936-check replay remains authoritative, so hash collisions can cost latency
but cannot certify a wrong candidate.

Status: `Production`.

### 6. Streaming pipeline and bounded S1 work

Capture/prepare/reduce stages, tagged synchronous-RAM responses, per-beat
valid masks, and a two-level four-way minimum tournament remove RAM bubbles
and long serial min-selection cones.

| Work measure | Before | After | Reduction |
| --- | ---: | ---: | ---: |
| Certified RTL replay | 40,681 cycles | 32,618 cycles | 19.8% |
| Former baseline to final replay | 94,320 cycles | 32,618 cycles | 65.4% |
| Primary sweep cap | 30 | 10 | 66.7% |
| Full configured S1 cap | 120 | 84 | 30.0% |
| Modeled four-bank primary rate at 45 MHz | 1,016/s | 3,049/s | 3.0x |

Status: cycle reductions are validated in RTL/model work; the board headline
comes from historical 51 MHz captures and reports roughly 994 us FPGA mean
core time. The current build target is 40.5 MHz.

### 7. Compressed streamed S2 working set

The full Paper S2 graph is 67,824 variables, 1,728 checks, and 391,464 edges.
The streamed quotient uses 942 variable orbits for X (941 for Z), keeps at
most 9,432 live posterior variables, and retires decisions into a one-bit
correction bitmap. No full edge-message image is kept on chip.

| S2 representation | X | Z |
| --- | ---: | ---: |
| Explicit topology | 6,654,888 bits | 6,652,440 bits |
| Quotient/template topology | 114,287 bits | 114,230 bits |
| Topology compression | 58.23x | 58.24x |
| On-chip working set | 287,591 bits / 16 BRAM blocks | 287,462 bits / 16 BRAM blocks |
| Resident logical projection | 2 BRAM blocks | 1 BRAM block |

Status: `Model`. This is the compressed streamed-S2 design behind the host
tail work, not a claim that S2R is currently resident on the board.

### 8. S2R message packing and neutral-component compression

The software/SDRAM model packs two seven-bit signed Relay messages into one
16-bit halfword instead of wasting a full halfword per message.

| X dynamic S2R layout | Before | Packed | Reduction |
| --- | ---: | ---: | ---: |
| Message layout | 1,054,224 bytes | 662,760 bytes | 37.1% |
| Ideal words per Relay iteration | 850,752 | 557,154 | 34.5% |

Terminal logical-neutral completion adds a rank-66 bridge for 72 detached
components using a 22,326-bit immutable plan, modeled as two reusable BRAM
blocks. The bridge fixes disconnected syndrome components without changing
logical coset, so it is a bounded state addition rather than a full graph
expansion. Neutral completion reduced seven hard-trap Relay work from 111 to
70 iterations (36.9%) in the model.

Status: `Model`; packed S2R is not the current board datapath.

### 9. Resident C tail instead of per-shot high-level fallback

The rare defer path was reduced from selectable Python/vectorized/native/WSL
fallback behavior to one persistent C worker. The worker uses a quotient image,
cached gamma tables, no Python/NumPy allocation in its hot loop, a 32-set fast
pass, bounded 240-set fallback, and a four-candidate portfolio.

Status: `Production`, but not yet fast enough for the endpoint gate: the
50,000-shot board capture measured about 50 ms mean CPU handoff.

## Current architecture

```text
Stim/Relay shot
    -> host packs 936 detector bits into 117 UART bytes
    -> FPGA four-lane Paper S1W at 40.5 MHz release target
       -> common-case logical word
       -> rare defer with full syndrome already held by host
    -> persistent C Relay-lite tail
    -> endpoint logical word and exact-syndrome status
```

## Release workflow

Requirements: Windows 11, Python 3.11+, Gowin EDA, WSL2 with GCC/OpenMP,
Tang Nano 20K, and packages in `requirements.txt`.

Set a non-default Gowin installation path:

```powershell
$env:GOWIN_HOME = 'C:\Gowin\Gowin_V1.9.11.03_Education_x64'
```

Build and program volatile SRAM:

```powershell
powershell -ExecutionPolicy Bypass -File tools\build_board.ps1 -Basis X
powershell -ExecutionPolicy Bypass -File tools\flash_gowin.ps1 -Basis X -Mode sram
```

Run smoke or proof:

```powershell
powershell -ExecutionPolicy Bypass -File tools\run_board_proof.ps1 `
  -Port COM6 -Basis X -Shots 100 -Smoke
powershell -ExecutionPolicy Bypass -File tools\run_board_proof.ps1 `
  -Port COM6 -Basis X -Shots 300000
```

SPI flash requires explicit `-Mode flash`. The flash script accepts only the
exact basis-specific production bitstream by default.

## Verification

```powershell
python -m pytest tests -q
python tools/run_iverilog.py --output build\sim\paper_s1w_uart.vvp `
  --top tb_tang_nano_20k_paper_s1w_four_lane_uart_top `
  --filelist tools\rtl_s1w_four_lane_uart_top.f
verilator --lint-only --language 1800 -DMITTEN_SIM `
  --top-module tang_nano_20k_paper_s1w_four_lane_uart_fast_51_top `
  -f tools\rtl_s1w_four_lane_uart_top.f
python tools/profile_c_tail.py --basis both --shots 1000 `
  --set-iterations 50 --output build\c_tail_profile.json
```

Current focused validation: `58 passed, 16 skipped, 12 subtests passed`.
Icarus compile and Verilator lint pass; the recorded X/Z Gowin build results
are summarized above. The authoritative board evidence and failure audit are
in
[`reports/HEADLINE_BENCHMARKS.md`](reports/HEADLINE_BENCHMARKS.md).

Generated projects, ROMs, bitstreams, and benchmark JSON live under ignored
`build/` and are intentionally not checked in. The repository keeps the small
frozen S1 inputs and a legacy simulation image payload; the production build
uses a portable payload only when its source hash matches, otherwise it
requires the pinned Relay fixture. Stale images are rejected.
Exploratory fixtures, task logs, and generated captures were removed.
