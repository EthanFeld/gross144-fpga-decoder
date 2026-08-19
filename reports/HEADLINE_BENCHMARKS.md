# Board endpoint evidence

Updated: 2026-08-18. Target: Gross144 at `p=0.2%`, Tang Nano 20K,
historical 51 MHz FPGA captures, 3 Mbaud UART, resident C Relay-lite tail.
The current release clock source is config/board_clock.json at 40.5 MHz;
historical captures must not be reinterpreted at the new clock.

## Stopping gates

| Gate | Target | Current status |
| --- | ---: | --- |
| Endpoint block LER | `<= 1e-5` | not proven |
| Mean endpoint core latency | `<= 1 ms` | not met; CPU tail dominates |
| FPGA mean core latency | `<= 1 ms` | met in clean captures |
| Board proof | large-shot, no crash/state loss | open |

## Clean board captures

All captures below used SRAM programming, COM6, 3 Mbaud, and the production
fast-handoff architecture. A zero-failure sample is functional evidence, not a
statistical LER pass; the one-sided 95% upper bound is the release statistic.

| Artifact | Shots | Endpoint failures | Point LER | One-sided 95% upper | FPGA mean core | Endpoint mean core |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 20,000-shot post-flash capture | 20,000 | 0 | 0 | `1.4978e-4` | `996.89 us` | `3.488 ms` |
| 50,000-shot capture | 50,000 | 0 | 0 | `5.9913e-5` | `993.76 us` | `3.515 ms` |

The raw capture JSON is generated output and is intentionally omitted from
the portfolio tree; the measured values and statistical bounds above are the
checked-in release record.

The clean 50,000-shot run had no live FPGA failures, transport errors, or CPU
tail errors. Its endpoint gate remains red because the confidence bound is
above `1e-5` and the mean endpoint latency is above `1 ms`.

## Long-run incident and failure audit

Two 300,000-shot attempts entered a repeatable cascade after a long clean
prefix: immediate low-cycle failures and rapidly increasing failure count.
The run was stopped after an unrelated crash/state-loss event, so no final
300,000-shot JSON is valid proof. The surviving prefix is useful bring-up
evidence only.

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

The host tail is pinned to C. It uses a 32-set fast pass, a bounded 240-set
fallback for ambiguous cases, and a four-leg portfolio. No auto backend,
vectorized fallback, native Relay wheel, or alternate WSL worker is allowed in
the release path.

## Repository cleanup status

The canonical build, SRAM flash, smoke, and proof wrappers are now checked in:

- `tools/build_board.ps1`
- `tools/flash_gowin.ps1`
- `tools/run_board_proof.ps1`
- `tools/gowin_paper_gross144_s1w_four_lane_uart.tcl`

Both basis builds were rerun after the cleanup. Gowin completed synthesis,
place-and-route, timing analysis, and bitstream generation for the exact
production closure:

| Basis | Logic | BSRAM | Bitstream SHA-256 |
| --- | ---: | ---: | --- |
| X | 19,735/20,736 (96%) | 39/46 (85%) | `52ED2718D6693816452AFF05FED3BAC02E32BC9BBBF0C4D4417887D91D7BC7A6` |
| Z | 19,434/20,736 (94%) | 39/46 (85%) | `7911D08258EB4E87C01A533999B3E3205FACBE0381B27D34062C712D931BAF25` |

These are historical build proofs, not current release artifacts: neither
image was flashed in this cleanup turn. The current 40.5 MHz target cannot be
rebuilt until the pinned Relay fixture is restored; the build now rejects the
stale tracked ROM manifest instead of silently reusing it.

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
