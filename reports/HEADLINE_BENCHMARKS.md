# Board endpoint evidence

Updated: 2026-08-19. Target: Gross144 at `p=0.2%`, Tang Nano 20K,
40.5 MHz timing-clean FPGA clock, 3 Mbaud UART, resident C Relay-lite tail.
Both basis images were built and SRAM-flashed on the connected GW2AR-18C.

## Stopping gates

| Gate | Target | Current status |
| --- | ---: | --- |
| Endpoint block LER | `<= 1e-5` | not proven |
| Mean endpoint core latency | `<= 1 ms` | not met; CPU tail dominates |
| FPGA mean core latency | `<= 1 ms` | not met at 40.5 MHz: 1.235–1.255 ms |
| Board proof | large-shot, no crash/state loss | 20k/basis clean; 300k open |

## Clean board captures

All captures below used SRAM programming, COM6, 3 Mbaud, and the production
fast-handoff architecture. A zero-failure sample is functional evidence, not a
statistical LER pass; the one-sided 95% upper bound is the release statistic.

| Artifact | Shots | Endpoint failures | Point LER | One-sided 95% upper | FPGA mean core | Endpoint mean core | C tail mean |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| X release candidate | 20,000 | 0 | 0 | `1.4978e-4` | `1.255 ms` | `2.642 ms` | `27.3 ms` |
| Z release candidate | 20,000 | 0 | 0 | `1.4978e-4` | `1.235 ms` | `2.791 ms` | `32.0 ms` |

The raw capture JSON is generated output and is intentionally omitted from
the portfolio tree; the measured values and statistical bounds above are the
checked-in release record.

Both runs had zero parser, transport, decoder, or endpoint failures. The
endpoint gate remains red because the confidence bound is above `1e-5` and
the mean endpoint latency is above `1 ms`.

The default `FAST_FIRST` C-tail shortcut was compared with the conservative
portfolio on 1,016 X and 973 Z real FPGA-deferred syndromes: zero acceptance,
syndrome, or logical mismatches. Check-node OpenMP parallelism was verified on
the same corpus; measured C-tail speedups were 3.09x (X) and 2.68x (Z).

## Long-run incident and failure audit

The prior 300,000-shot attempts entered a repeatable cascade after a long
clean prefix. The current explicit `S_ERROR` recovery and basis/image guard
must be exercised by a fresh 300,000-shot campaign; no 300,000-shot JSON is
currently valid proof.

The earlier completed 300,000-shot capture contained five board-only logical
mismatches. Exact saved-case replay classified them as follows:

| Shot | FPGA result | Classification |
| ---: | --- | --- |
| 77,511 | `0x7D5` vs truth `0xBD5` | board posterior/logical projection mismatch |
| 120,106 | `0x867` vs truth `0x22D` | shared S1W terminal wrong-coset acceptance; host C tail has truth-labelled rescue |
| 140,382 | `0x663` vs truth `0xA63` | board posterior/logical projection mismatch |
| 205,295 | `0x46C` vs truth `0x057` | board rescue-path projection mismatch |
| 235,137 | `0x0D6` vs truth `0xC97` | board rescue-path projection mismatch |

These failures had valid transport and no CPU handoff. The current C tail is
therefore not blamed for those board-only cases. The live campaign now prints
the first eight complete failure records, including parser counters and CPU
tail status, to make the remaining incident reproducible.

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
| X | 40.500 MHz | 45.353 MHz | `+2.642 ns` | `+0.091 ns` | `5AF52453BE02E5486085A2355181440E6E57B6DD9716A73FEBD918B1A993639C` |
| Z | 40.500 MHz | 44.559 MHz | `+2.249 ns` | `+0.219 ns` | `B043D695EFC3F7EDAA46E4CA709EC98034865111EF24E84C0E782023B0A68B5E` |

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
