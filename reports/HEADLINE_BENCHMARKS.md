# Board endpoint evidence

Updated: 2026-08-19. Target: Gross144 at `p=0.2%`, Tang Nano 20K,
40.5 MHz timing-clean FPGA clock, 3 Mbaud UART, resident C Relay-lite tail.
Both basis images were built and SRAM-flashed on the connected GW2AR-18C.

## Stopping gates

| Gate | Target | Current status |
| --- | ---: | --- |
| Endpoint block LER | `<= 1e-5` | Z passes; X open after host interruption |
| Mean endpoint core latency | `<= 1 ms` | not met; CPU tail dominates |
| FPGA mean core latency | `<= 1 ms` | not met at 40.5 MHz: 1.235–1.255 ms |
| Board proof | large-shot, no crash/state loss | Z 300k clean; X interrupted |

## Clean board captures

All captures below used SRAM programming, COM6, 3 Mbaud, and the production
fast-handoff architecture. A zero-failure sample is functional evidence, not a
statistical LER pass; the one-sided 95% upper bound is the release statistic.

| Artifact | Shots | Endpoint failures | Point LER | One-sided 95% upper | FPGA mean core | Endpoint mean core | C tail mean |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| X release candidate | 30,000 | 0 | 0 | `9.9853e-5` | `1.221 ms` | `2.687 ms` | `34.24 ms` |
| Z 300k statistical run | 300,000 | 0 | 0 | `9.9857e-6` | `1.202 ms` | `2.294 ms` | `26.67 ms` |

The raw capture JSON is generated output and is intentionally omitted from
the portfolio tree; the measured values and statistical bounds above are the
checked-in release record.

Both 30k validation runs had zero parser, transport, decoder, or endpoint
failures. Z then completed 300k with zero failures, zero parser/transport
errors, 12,284 defers, and 12,284 exact C-tail accepts. Z LER gate passes;
endpoint gate remains red because mean endpoint latency is above `1 ms`.

The default `FAST_FIRST` C-tail shortcut was compared with the conservative
portfolio on 1,016 X and 973 Z real FPGA-deferred syndromes: zero acceptance,
syndrome, or logical mismatches. Check-node OpenMP parallelism was verified on
the same corpus; measured C-tail speedups were 3.09x (X) and 2.68x (Z).

## Long-run incident and failure audit

X 300k attempt was interrupted by host-side COM6 loss; transport avalanche is
excluded from decoder LER. Z 300k completed cleanly. A bounded COM6
close/reopen/retry handles transient FTDI `WriteFile` denial; unrecovered
transport faults still invalidate a run.

The earlier completed 300,000-shot capture contained five board-only logical
mismatches. Exact saved-case replay classified them as follows:

| Shot | FPGA result | Classification |
| ---: | --- | --- |
| 77,511 | `0x7D5` vs truth `0xBD5` | fixed by exact streamed replay before logical projection |
| 120,106 | `0x867` vs truth `0x22D` | fixed by terminal-budget defer; host C tail now receives the candidate |
| 140,382 | `0x663` vs truth `0xA63` | board posterior/logical projection mismatch |
| 205,295 | `0x46C` vs truth `0x057` | board rescue-path projection mismatch |
| 235,137 | `0x0D6` vs truth `0xC97` | board rescue-path projection mismatch |

The first two cases were replayed on the current X image: the FPGA now
returns a verified defer and the resident C tail returns the truth word. The
remaining three historical cases are retained as regression evidence and are
not counted as a current pass until replayed under the final image. The live
campaign prints the first eight complete failure records, including parser
counters and CPU-tail status.

## Current architecture

```text
936-bit syndrome -> FPGA four-lane S1W at 40.5 MHz release target
                     | accepted
                     v
                 logical word
                     |
                     + deferred full 1,728-bit syndrome
                           v
                    resident C Relay-lite tail
                           v
                       endpoint word
```

The host tail is pinned to C. It uses a verified config-0 fast-first pass, a
32-set fast pass, a bounded 240-set fallback for ambiguous cases, and a
four-leg conservative portfolio for audit/A-B mode. No auto backend,
vectorized fallback, native Relay wheel, or alternate WSL worker is allowed in
the release path.

## Repository cleanup status

The canonical build, SRAM flash, smoke, and proof wrappers are now checked in:

- `tools/build_board.ps1`
- `tools/flash_gowin.ps1`
- `tools/run_board_proof.ps1`
- `tools/gowin_paper_gross144_s1w_four_lane_uart.tcl`

Both basis builds completed synthesis, place-and-route, timing analysis, and
bitstream generation. The build rejects negative setup/hold slack:

| Basis | Requested clock | Achieved Fmax | Setup slack | Hold slack | Bitstream SHA-256 |
| --- | ---: | ---: | ---: | ---: | --- |
| X | 40.500 MHz | 40.533 MHz | `+0.020 ns` | `+0.202 ns` | `A875EACBB244F9CA9A4408C28545E51BAD5B522DC090F083A2742E9E73E28303` |
| Z | 40.500 MHz | 41.445 MHz | `+0.563 ns` | `+0.077 ns` | `A74BBA39F7E5CBC2C51A75834FCEA8F06D7C4E994E9CD9A307C0C3CCBE96FFB8` |

Gowin reports a `PR1014` generic-routing warning for the 27 MHz input clock;
post-route setup/hold timing passes at the requested 40.5 MHz.

Legacy source families were removed from the release tree. Approximate file
count reduction from the pre-cleanup inventory is:

| Area | Before | Current | Removed |
| --- | ---: | ---: | ---: |
| Python modules | 56 | 27 | 51.8% |
| RTL files | 139 | 20 | 85.6% |
| Tools | 96 | 13 | 86.5% |
| Tests | 162 | 16 | 90.1% |
| Combined source/tool/test inventory | 453 | 76 | **83.2%** |

The remaining files are the production FPGA closure, resident C-tail support,
focused software references/tests, and frozen input/evidence data. Historical
binary fixtures, task reports, and generated captures were removed; they are
not build inputs or release code.
