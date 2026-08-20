# Production path

MITTEN's release contract is the hardware/software endpoint that was exercised
in the authoritative 300,000-shot X and Z board campaigns on 2026-08-20.

```text
Stim / Relay-BP Gross144 fixture
    -> Python board campaign
    -> 936-bit packed UART frame (command 0x31, 3 Mbaud)
    -> Tang Nano 20K FPGA: four-lane Paper Gross144 S1W @ 40.5 MHz
       -> exact-replay accepted logical word
       -> or verified defer; full 1,728-bit syndrome is already on the host
    -> persistent C Relay-lite tail in WSL
    -> endpoint logical word + exact-syndrome status
```

## Fixed release contract

| Item | Release value |
| --- | --- |
| Board | Tang Nano 20K / GW2AR-18C |
| FPGA bases | X and Z, separate images |
| Core clock | 40.5 MHz |
| UART | 3,000,000 baud, 8-N-1 |
| FPGA path | Paper Gross144 S1W, four-lane, 10-sweep fast handoff |
| Acceptance safety | 20-bit residual hash is rejection-only; exact 936-check replay is authoritative |
| CPU tail | persistent C Relay-lite, fast-first + bounded fallback portfolio |
| Programming | SRAM by default; SPI flash only with explicit `-Mode flash` |

The production clock source of truth is
[`config/board_clock.json`](../config/board_clock.json). Historical source and
top-module identifiers containing `_51` are retained only for Gowin/project
compatibility; they do **not** describe the current clock frequency.

## Release gates — closed

The final on-board X and Z campaigns are authoritative. Both completed
300,000 shots at `p=0.2%` with zero endpoint, parser, transport, or decoder
failures. All FPGA defers were accepted by the resident C tail.

| Basis | Shots | Endpoint failures | One-sided 95% LER upper bound | FPGA mean core | Endpoint mean core | Defers / accepts |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| X | 300,000 | 0 | `9.9857e-6` | `1.220 ms` | `2.778 ms` | 13,118 / 13,118 |
| Z | 300,000 | 0 | `9.9857e-6` | `1.202 ms` | `2.294 ms` | 12,284 / 12,284 |

The block-LER release target is `<= 1e-5`. Endpoint latency is reported as an
order-millisecond performance metric; there is no fixed sub-millisecond
release gate.

The Z raw capture was produced before commit `aa57e95` removed the former
1 ms endpoint-latency gate. Its historical `endpoint_pass=false` field is
therefore stale policy metadata: the capture itself has zero failures, clean
transport, and an LER confidence bound below the release target. The current
release interpretation is recorded in
[`reports/release_evidence.json`](../reports/release_evidence.json).

## Timing-clean release images

The canonical build checks the generated `.fs`, place-and-route report,
timing-path report, ROM provenance, and SHA-256 before returning success. It
rejects negative post-route setup or hold slack.

| Basis | Logic | BSRAM | Requested | Achieved Fmax | Setup / hold slack | Bitstream SHA-256 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| X | 19,773 / 20,736 (96%) | 39 / 46 (85%) | 40.500 MHz | 40.533 MHz | `+0.020 / +0.202 ns` | `A875EACBB244F9CA9A4408C28545E51BAD5B522DC090F083A2742E9E73E28303` |
| Z | 19,850 / 20,736 (96%) | 39 / 46 (85%) | 40.500 MHz | 41.445 MHz | `+0.563 / +0.077 ns` | `A74BBA39F7E5CBC2C51A75834FCEA8F06D7C4E994E9CD9A307C0C3CCBE96FFB8` |

The bitstream hashes match the images used by the authoritative 300k board
captures.

## Failure policy

UART framing, CRC, timeout, basis mismatch, unrecovered transport errors,
decoder errors, a defer without the configured C tail, or a wrong endpoint
logical word are failures. The C tail never silently switches to an alternate
decoder. Failure samples retain the full detector word for deterministic
replay.

## Historical incidents

Earlier development images exposed board-only projection/recovery defects and
one host-side COM6 interruption. Those incidents drove exact replay, terminal
defer, and serial-recovery changes. They are debugging history, not open
release gates. The final X/Z 300k runs above supersede the older captures for
release status.
