# MITTEN Gross144 board endpoint

MITTEN is a hardware/software qLDPC decoder prototype for the Gross
`[[144,12,12]]` bivariate-bicycle code. It maps the common decoding path onto a
Tang Nano 20K (`GW2AR-18C`) FPGA and sends only rare hard cases to a persistent
C Relay-lite tail on the host.

The project is primarily an FPGA architecture and verification exercise: it
compresses the Paper Gross144 S1 topology by **44.3x**, schedules the decoder
across four banked lanes, uses exact syndrome replay as the FPGA acceptance
certificate, and closes timing at a **40.5 MHz** production clock.

**Release status:** the final physical-board X and Z campaigns each completed
**300,000 shots at `p=0.2%` with zero endpoint failures**. Their one-sided 95%
block-LER upper bound is `9.9857e-6`, meeting the `1e-5` release target.

```text
936 selected detector bits
        |
        v
  Tang Nano 20K FPGA
  four-lane S1W @ 40.5 MHz
        | accepted             | verified defer
        v                      v
   logical word        full 1,728-bit syndrome on host
                               |
                               v
                       persistent C Relay-lite tail
                               |
                               v
                         endpoint logical word
```

Supported release images are basis `X` and basis `Z`. Historical B3, H03,
probe, alternate-clock, and Open975 paths are not release interfaces.

For the release contract and exact evidence provenance, see
[`docs/PRODUCTION_PATH.md`](docs/PRODUCTION_PATH.md) and
[`reports/release_evidence.json`](reports/release_evidence.json).

## Current headline numbers

Target: `p = 0.2%`, block LER `<= 1e-5`; endpoint latency measured in
order-ms, no sub-ms gate.

| Board capture | Shots | Endpoint failures | Point LER | One-sided 95% upper bound | FPGA mean | Endpoint mean | C tail mean |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| X 300k statistical run | 300,000 | 0 | 0 | `9.9857e-6` | `1.220 ms` | `2.778 ms` | `35.64 ms` |
| Z 300k statistical run | 300,000 | 0 | 0 | `9.9857e-6` | `1.202 ms` | `2.294 ms` | `26.67 ms` |

Both X/Z 300k runs pass statistical LER target: zero endpoint, transport,
parser, or decoder errors; all 25,402 C-tail defers were accepted. Endpoint
latency is dominated by CPU tail, remains acceptable at order-ms scale.

Paper comparison numbers supplied for this target:

| Metric | Paper reference | Current board evidence |
| --- | ---: | ---: |
| Block LER | `1.9e-6` | X/Z: `<= 9.9857e-6` at 300k |
| LER / syndrome-extraction round | `1.6e-7` | not established on board |
| LER / round / logical qubit | `1.3e-8` | not established on board |

X/Z meet statistical LER target at 300,000 shots. X endpoint is `2.778 ms`,
Z `2.294 ms`; both accepted as order-ms performance. Latency remains reported,
not pass/fail.

## Build status

Current basis-specific build/timing proofs:

| Basis | Requested clock | Achieved Fmax | Setup / hold slack | Bitstream SHA-256 |
| --- | ---: | ---: | ---: | --- |
| X | 40.500 MHz | 40.533 MHz | `+0.020 / +0.202 ns` | `A875EACBB244F9CA9A4408C28545E51BAD5B522DC090F083A2742E9E73E28303` |
| Z | 40.500 MHz | 41.445 MHz | `+0.563 / +0.077 ns` | `A74BBA39F7E5CBC2C51A75834FCEA8F06D7C4E994E9CD9A307C0C3CCBE96FFB8` |

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

Status: cycle reductions are validated in RTL/model work; current 40.5 MHz
board captures report 1.202–1.220 ms FPGA mean core time. The prior 51 MHz
captures are retained only as historical comparison.

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

Status: `Production`. The default fast-first C tail is equivalence-clean on
1,016 X and 973 Z hardware-deferred syndromes. In the authoritative 300k board
runs, mean C-tail handoff latency was 35.64 ms (X) and 26.67 ms (Z).

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

## Reproduce and verify

Portable development requires Python 3.11+ plus Icarus Verilog and Verilator
for RTL checks. Install the Python dependencies and run the portable suite:

```bash
python -m pip install -r requirements.txt
python -m pip install -e .
python -m pytest tests -q
make test-rtl-recovery
make lint
```

The public Relay-BP Gross144 fixture is deliberately not vendored. Fetch and
verify the exact pinned checkout used by this project:

```bash
python tools/bootstrap_relay.py
python -m pytest tests -q
```

The bootstrap script checks out Relay-BP commit
`19d7023d476248858fc01bdf087ce673feaa4ef4` under ignored `build/relay` and
verifies the expected Stim fixture hashes before tests or image regeneration
use it.

GitHub Actions runs Python 3.11/3.12 tests, the pinned-fixture contract, Icarus
RTL recovery simulation, and Verilator lint. Gowin place-and-route and the
large-shot endpoint proof remain hardware operations; CI is not presented as a
substitute for board evidence.

### Board build and proof

Requirements: Windows 11, Python 3.11+, Gowin EDA, WSL2 with GCC/OpenMP, a
Tang Nano 20K, and the packages in `requirements.txt`.

Set a non-default Gowin installation path if needed:

```powershell
$env:GOWIN_HOME = 'C:\Gowin\Gowin_V1.9.11.03_Education_x64'
```

Bootstrap the pinned Relay fixture, build, and program volatile SRAM:

```powershell
python tools\bootstrap_relay.py
powershell -ExecutionPolicy Bypass -File tools\build_board.ps1 -Basis X
powershell -ExecutionPolicy Bypass -File tools\flash_gowin.ps1 -Basis X -Mode sram
```

Run smoke or a full proof campaign:

```powershell
powershell -ExecutionPolicy Bypass -File tools\run_board_proof.ps1 `
  -Port COM6 -Basis X -Shots 100 -Smoke
powershell -ExecutionPolicy Bypass -File tools\run_board_proof.ps1 `
  -Port COM6 -Basis X -Shots 300000
```

Repeat for basis `Z`. SPI flash requires explicit `-Mode flash`; the flash
wrapper accepts only the exact basis-specific production bitstream by default.

Generated Gowin projects, ROMs, bitstreams, raw captures, and benchmark output
live under ignored `build/`. The repository tracks compact machine-readable
release evidence in [`reports/release_evidence.json`](reports/release_evidence.json)
instead of committing the large raw campaign files.

## References and attribution

MITTEN builds on public work on bivariate-bicycle qLDPC codes and Relay-BP.
See [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) for the pinned Relay-BP
fixture, papers, public Gross-code reference, and license/provenance notes.

## License

MITTEN is released under the Apache License 2.0. See [`LICENSE`](LICENSE).
