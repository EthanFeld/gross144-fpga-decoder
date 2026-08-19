# Production path

```text
Stim/Relay shot
    -> Python board campaign
    -> 936-bit packed UART frame (command 0x31)
    -> FPGA S1W, four parallel lanes, 40.5 MHz release target
       -> accepted logical word
       -> deferred full detector word
    -> resident C Relay-lite worker in WSL
    -> endpoint logical word and exact-syndrome status
```

## Fixed release contract

| Item | Value |
| --- | --- |
| Board | Tang Nano 20K / GW2AR-18C |
| FPGA bases | X and Z, separate images |
| Core clock | 40.5 MHz |
| UART | 3,000,000 baud, 8-N-1 |
| FPGA path | Paper Gross144 S1W, four-lane, 10-sweep fast handoff |
| CPU tail | resident C Relay-lite, 32-set fast pass, bounded 240-set fallback, 4-leg portfolio |
| Programming | SRAM by default; SPI flash only with explicit `-Mode flash` |

The FPGA bitstream and the host tail are versioned independently in the
campaign evidence: the JSON records both configuration hashes and the exact
bitstream SHA-256 when `--bitstream` is supplied.

## Failure policy

- UART framing, CRC, timeout, basis mismatch, decoder error, deferred-without-tail,
  and wrong logical word are endpoint failures.
- The C tail never silently falls back to an alternate decoder.
- A finite zero-failure run reports its confidence upper bound; it is not an
  endpoint-gate pass unless that bound and latency gate pass.
- Failure samples retain the full detector word for deterministic replay.

## Known open gate

The current board image has clean short captures, but the long run encountered
an unrelated crash/state-loss event and four board-only logical mismatches plus
one shared terminal wrong-coset case are still under investigation. The large
proof gate therefore remains open.

## Current build artifacts

The canonical build flow checks the exact .fs, P&R report, timing-path
report, ROM provenance, and SHA-256 before returning success. A build is
rejected when the pinned Relay fixture is unavailable or post-route setup/hold
slack is negative.

| Basis | Logic | BSRAM | Bitstream SHA-256 |
| --- | ---: | ---: | --- |
| X | 19,735/20,736 (96%) | 39/46 (85%) | `52ED2718D6693816452AFF05FED3BAC02E32BC9BBBF0C4D4417887D91D7BC7A6` |
| Z | 19,434/20,736 (94%) | 39/46 (85%) | `7911D08258EB4E87C01A533999B3E3205FACBE0381B27D34062C712D931BAF25` |

The recorded hashes above belong to historical 51 MHz images and are retained
as evidence only. The current 40.5 MHz target has not been rebuilt after the
cleanup because the pinned Relay fixture is absent; no timing-clean bitstream
claim is made until that fixture and a board are available.
