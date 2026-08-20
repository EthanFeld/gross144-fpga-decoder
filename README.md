# Gross144 FPGA Decoder

Cheap FPGA decoding for the Gross `[[144,12,12]]` qLDPC
memory.

This repository implements the common decoding path for the Gross
bivariate-bicycle code on a Tang Nano 20K (`GW2AR-18C`). It implements a highly 
compressed version of min-sum belief propagation, executes check updates across
four banked FPGA lanes, verifies accepted candidates by exact syndrome replay,
and routes terminal defers to a persistent C Relay-lite tail on the host.


This is heavily inspired by the telescoping decoder used in the Mitten codes paper (Arxiv:2607.28795)

All data is done on a basic 0.2% uniform error rate model.
## At a glance

| Item | X | Z |
| --- | ---: | ---: |
| Code / basis | Gross `[[144,12,12]]` / X memory | Gross `[[144,12,12]]` / Z memory |
| Physical error rate | `0.002` | `0.002` |
| Board shots | 300,000 | 300,000 |
| Endpoint failures | **0** | **0** |
| One-sided 95% block-LER upper bound | `9.9857e-6` | `9.9857e-6` |
| FPGA defer rate | `4.37%` | `4.09%` |
| Mean FPGA core latency | `1.220 ms` | `1.202 ms` |
| Mean hybrid core latency | `2.778 ms` | `2.294 ms` |
| Mean endpoint wall time | `18.003 ms` | `17.611 ms` |
| FPGA clock | `40.5 MHz` | `40.5 MHz` |
| Logic | 19,773 / 20,736 (`96%`) | 19,850 / 20,736 (`96%`) |
| BSRAM | 39 / 46 (`85%`) | 39 / 46 (`85%`) |

The 300k runs are in
[`reports/release_evidence.json`](reports/release_evidence.json), including
capture provenance, bitstream hashes, timing data, defer counts, and the LER
confidence bound.

## Architecture

```text
Stim / Relay Gross144 circuit
        |
        | full 1,728-detector sample + true observables retained by host
        |
        +---- select and pack 936 basis-specific detector bits
                              |
                              v
                    3 Mbaud framed UART
                              |
                              v
                    Tang Nano 20K FPGA
                four-lane S1W @ 40.5 MHz
                  /                     \
        syndrome-consistent          terminal defer
          fast-path result                |
                  |                        +---- full detector word
                  |                              already retained on host
                  |                                      |
                  |                                      v
                  |                           persistent C Relay-lite tail
                  |                                      |
                  +----------------------+---------------+
                                         v
                                endpoint logical word
                                         |
                                         v
                           compare with Stim observables
```

Production flow:

1. Stim samples the pinned Gross144 circuit. The host retains the full
   1,728-detector word and hidden 12-bit observable word for evaluation.
2. The host selects 936 basis-specific detector bits and packs them into
   117 UART bytes.
3. The FPGA runs the fixed-point, four-lane Paper Gross144 S1W decoder.
4. The resident 20-bit residual hash is rejection-only. Accepted candidates
   are replayed against all 936 selected checks before acceptance.
5. A terminal defer invokes one persistent C Relay-lite worker using the full
   detector word already held by the host.
6. The endpoint logical word is compared with Stim's hidden observables.

Exact replay establishes syndrome consistency for FPGA-accepted candidates;
logical performance is measured independently against the hidden observables.

## Why the FPGA fits

The direct Stage-1 representation has 8,784 variables, 936 checks, and 30,672
edges. Its explicit edge topology alone requires 429,408 bits. The Gross code's
`Z12 x Z6` translation action has order 72 and reduces the stored topology to
122 variable orbits plus reusable translated templates.

| Stage-1 representation | Storage |
| --- | ---: |
| Explicit edge topology | 429,408 bits |
| Quotient/template topology | 9,694 bits |
| Compression | **44.3x** |

The production image combines several reductions:

- **Transport projection:** 1,728 detector bits become 936 basis-selected bits,
  packed into 117 bytes: 45.8% fewer transmitted bits.
- **Static four-bank coloring:** `(orbit_color + x) mod 4` maps translated
  variables to banks without runtime conflict search.
- **Bounded schedule:** maximum template degree is 35, requiring at most nine
  four-edge beats per check.
- **Compressed check state:** `min1`, `min2`, `argmin`, signs, and validity
  replace a full edge-message store.
- **Paired four-lane execution:** disjoint checks run through paired banked
  engines without duplicating the graph.
- **Fixed-point datapath:** production posteriors are signed 11-bit values and
  Stage-1 messages use five-bit sign/magnitude representation.
- **Rejection-only residual hash:** a 20-bit projection can reject candidates
  early, but never replaces exact 936-check replay.
- **Streaming pipeline:** tagged RAM responses, valid masks, and two-level
  minimum tournaments remove avoidable memory bubbles and serial min cones.

The compiler-produced topology statistics are tracked in
[`artifacts/paper_gross144_s1_templates_p002/summary.json`](artifacts/paper_gross144_s1_templates_p002/summary.json),
and the generated ROM contract is recorded in
[`images/manifest.json`](images/manifest.json).

### Compression ledger

The reductions below are measured against the direct Paper Gross144 Stage-1
representation. They apply to different resources, so the factors must not be
multiplied together into one headline number. The audited end-to-end topology
factor is **44.3x**; the other entries describe transport, state, or execution
reductions around that representation.

| Technique | Before | After | Factor / effect | What changes |
| --- | ---: | ---: | ---: | --- |
| **Translation-orbit quotient** | 8,784 variable instances | 122 reusable orbits | **72.0x** fewer topology instances | The `Z12 x Z6` translation action stores one orbit image and reuses it across 72 translations. |
| **Template topology encoding** | 429,408 explicit edge-address bits | 9,694 quotient/template bits | **44.3x** smaller | Reusable detector-time templates, orbit IDs, bank colors, anchors, and compact coordinates replace one 14-bit address for every edge. |
| **Detector transport projection** | 1,728 sampled detector bits | 936 basis-selected bits | **1.85x** smaller, 45.8% fewer bits | Only the initialization-basis detector slice needed by the selected FPGA image crosses the UART. |
| **Packed transport** | 936 individual bits | 117 UART payload bytes | **8 bits/byte**, no information loss | Packing changes framing/storage overhead; it is separate from the 1.85x detector projection. |
| **Compressed check state** | 30,672 edge messages × 5 bits = 153,360 bits | 43,776 bits of `min1`, `min2`, `argmin`, and signs | **3.50x** smaller | Two minima and one argmin replace per-edge magnitudes; one sign bit per edge is retained. |
| **BRAM state packing** | 12 BRAM blocks for warm-up edge messages | 3 BRAM blocks for compressed records | **4.0x** fewer blocks | Physical memory savings include BRAM granularity and packing, so this is slightly better than the raw 3.50x bit ratio. |
| **Residual rejection hash** | Full 936-bit candidate syndrome | 20-bit residual projection | **46.8x** smaller rejection state | The hash can reject impossible candidates early; exact 936-check replay is still required for acceptance. |
| **Four-bank coloring** | Runtime conflict search | Static `(orbit_color + x) mod 4` mapping | Eliminates runtime search | This is control/logic reduction rather than a byte-compression factor. |
| **Four-lane check execution** | One check lane | Four disjoint check lanes | Up to **4x** check parallelism | Compiler-proven variable disjointness permits concurrent updates without changing layered semantics. |
| **Four-edge beats** | Up to 35 serial edge accesses/check | At most 9 four-edge beats/check | Up to **3.89x** fewer beats at degree 35 | Degree handling is streamed and bounded rather than implemented as a large serial edge loop. |

The storage figures are generated by the compiler and can be reproduced from
[`artifacts/paper_gross144_s1_templates_p002/summary.json`](artifacts/paper_gross144_s1_templates_p002/summary.json).

## Physical-board results

The authoritative release runs used basis-specific SRAM-programmed production
images on a Tang Nano 20K at `p=0.002`.

| Metric | X 300k | Z 300k |
| --- | ---: | ---: |
| Shots | 300,000 | 300,000 |
| Endpoint logical failures | **0** | **0** |
| Parser CRC errors | 0 | 0 |
| Parser format errors | 0 | 0 |
| Serial reconnects | 0 | 0 |
| FPGA defers | 13,118 | 12,284 |
| FPGA fast-path fraction | `95.63%` | `95.91%` |
| C-tail accepts / defers | 13,118 / 13,118 | 12,284 / 12,284 |
| One-sided 95% block-LER upper bound | `9.9857e-6` | `9.9857e-6` |

Both bases meet the `1e-5` statistical block-LER target. Endpoint latency is
reported as an order-ms performance metric; there is no fixed sub-ms release
gate.

### Latency

| Metric | X | Z |
| --- | ---: | ---: |
| Mean FPGA core | `1.220 ms` | `1.202 ms` |
| FPGA p99 core | `2.829 ms` | `2.828 ms` |
| Mean FPGA board wall time | `16.444 ms` | `16.519 ms` |
| Mean C tail when invoked | `35.642 ms` | `26.672 ms` |
| Mean hybrid core | `2.778 ms` | `2.294 ms` |
| p99 hybrid core | `11.511 ms` | `12.046 ms` |
| Mean endpoint wall time | `18.003 ms` | `17.611 ms` |
| p99 endpoint wall time | `25.404 ms` | `26.005 ms` |

### Timing-clean images

The canonical build checks the generated `.fs`, place-and-route report,
timing-path report, ROM provenance, and SHA-256. It rejects negative setup or
hold slack.

| Basis | Logic | BSRAM | Requested | Achieved Fmax | Setup / hold slack | Release bitstream SHA-256 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| X | 19,773 / 20,736 (`96%`) | 39 / 46 (`85%`) | 40.500 MHz | 40.533 MHz | `+0.020 / +0.202 ns` | `A875EACB...E28303` |
| Z | 19,850 / 20,736 (`96%`) | 39 / 46 (`85%`) | 40.500 MHz | 41.445 MHz | `+0.563 / +0.077 ns` | `A74BBA39...96FFB8` |

Complete hashes and capture provenance are in
[`reports/release_evidence.json`](reports/release_evidence.json). Historical
source/top identifiers containing `_51` are retained for Gowin compatibility;
the production clock is 40.5 MHz.



## Repository layout

| Path | Contents |
| --- | --- |
| `rtl/` | Production SystemVerilog datapath, memories, protocol, and Tang Nano top |
| `python/gross144_decoder/` | Graph compiler, reference decoders, fixed-point models, host integration |
| `images/` | Frozen production ROMs and image manifest |
| `artifacts/` | Compiler-generated Gross144 quotient/template artifacts |
| `tests/` | Python reference/integration tests and RTL testbench |
| `tools/` | Relay bootstrap, image export, Gowin build/flash, board campaigns, C-tail tooling |
| `reports/` | Board benchmark summaries and machine-readable release evidence |
| `docs/` | Production contract, architecture, numeric format, and verification notes |

The `python/gross144_decoder/` package and `GROSS144_*` environment/schema
identifiers are the public interfaces for scripts and captured evidence.

## Reproduce software and RTL checks

Requirements: Python 3.11+, Git, Icarus Verilog, and Verilator.

```bash
python -m pip install -r requirements.txt
python -m pip install -e .
python tools/bootstrap_relay.py
python -m pytest tests -q
make test-rtl-recovery
make lint
```

`tools/bootstrap_relay.py` checks out Relay-BP commit
`19d7023d476248858fc01bdf087ce673feaa4ef4` under ignored `build/relay` and
verifies the pinned Gross144 Stim fixture hashes.

## Build, flash, and run the board endpoint

Board reproduction assumes Windows 11, Gowin EDA, WSL2 with GCC/OpenMP for
the persistent C tail, a Tang Nano 20K, and the Python dependencies above.

```powershell
$env:GOWIN_HOME = 'C:\Gowin\Gowin_V1.9.11.03_Education_x64'
python tools\bootstrap_relay.py
powershell -ExecutionPolicy Bypass -File tools\build_board.ps1 -Basis X
powershell -ExecutionPolicy Bypass -File tools\flash_gowin.ps1 -Basis X -Mode sram
```

Run a smoke test or full release campaign:

```powershell
powershell -ExecutionPolicy Bypass -File tools\run_board_proof.ps1 `
  -Port COM6 -Basis X -Shots 100 -Smoke

powershell -ExecutionPolicy Bypass -File tools\run_board_proof.ps1 `
  -Port COM6 -Basis X -Shots 300000
```

Repeat with `-Basis Z` for the Z image. SRAM programming is the default safe
workflow; SPI flash requires explicit `-Mode flash`.

Generated Gowin projects, ROMs, bitstreams, raw captures, and benchmark output
live under ignored `build/`. Compact release provenance is tracked in
[`reports/release_evidence.json`](reports/release_evidence.json) rather than
committing large campaign files.

## Release evidence and references

- [`docs/PRODUCTION_PATH.md`](docs/PRODUCTION_PATH.md) — board/software endpoint
  contract.
- [`reports/HEADLINE_BENCHMARKS.md`](reports/HEADLINE_BENCHMARKS.md) — readable
  physical-board evidence.
- [`reports/release_evidence.json`](reports/release_evidence.json) — capture,
  timing, bitstream, and SHA-256 provenance.
- [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) — pinned fixtures,
  licenses, and external references.

External references include Relay-BP, the Gross/bivariate-bicycle code data,
and the papers listed in `THIRD_PARTY_NOTICES.md`.

## License

Gross144 FPGA Decoder is released under the Apache License 2.0. See
[`LICENSE`](LICENSE).
