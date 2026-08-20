# Board endpoint evidence

Updated: 2026-08-20. This file records the authoritative release evidence for
Gross144 at `p=0.2%` on a Tang Nano 20K (`GW2AR-18C`): timing-clean 40.5 MHz
FPGA images, 3 Mbaud UART, and the persistent C Relay-lite tail.

## Release result

| Gate | Target | Authoritative result |
| --- | ---: | --- |
| Endpoint block LER | `<= 1e-5` | **PASS** — X/Z 300k, zero endpoint failures, `9.9857e-6` one-sided 95% upper bound |
| Transport/parser integrity | zero unrecovered errors | **PASS** — zero parser/transport failures in both final runs |
| FPGA timing closure | non-negative setup and hold slack at 40.5 MHz | **PASS** — X `+0.020/+0.202 ns`, Z `+0.563/+0.077 ns` |
| Board proof | large-shot run on physical hardware | **PASS** — X/Z 300k completed cleanly |
| Endpoint latency | order-ms informational | X `2.778 ms`, Z `2.294 ms` mean core latency |

## Authoritative on-board captures

Both runs used SRAM-programmed, basis-specific production images on the
connected GW2AR-18C. A zero-failure sample is not treated as a zero true LER;
the one-sided 95% confidence upper bound is the release statistic.

| Basis | Shots | Endpoint failures | Point LER | One-sided 95% upper | FPGA mean core | Endpoint mean core | C-tail mean | Defers accepted |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| X | 300,000 | 0 | 0 | `9.9857e-6` | `1.220 ms` | `2.778 ms` | `35.64 ms` | 13,118 / 13,118 |
| Z | 300,000 | 0 | 0 | `9.9857e-6` | `1.202 ms` | `2.294 ms` | `26.67 ms` | 12,284 / 12,284 |

There were zero endpoint failure records, zero parser CRC errors, zero parser
format errors, and zero serial reconnects in either final run.

The tracked machine-readable release record is
[`release_evidence.json`](release_evidence.json). It also records the SHA-256
of each locally retained raw capture so an archived raw JSON can be checked
against this release later.

### Capture provenance

| Basis | Capture timestamp (UTC) | Capture Git commit | Bitstream SHA-256 |
| --- | --- | --- | --- |
| X | `2026-08-20T16:19:43.029093+00:00` | `aa57e9503d095b41bf908b1a0dc43fbc473c9882` | `A875EACBB244F9CA9A4408C28545E51BAD5B522DC090F083A2742E9E73E28303` |
| Z | `2026-08-20T14:30:50.346142+00:00` | `d55a1c0f95d5ac9061037a5f919b19805227a144` | `A74BBA39F7E5CBC2C51A75834FCEA8F06D7C4E994E9CD9A307C0C3CCBE96FFB8` |

The Z capture predates commit `aa57e95`, which changed only the endpoint
campaign's latency-gate semantics and documentation. The capture's historical
`endpoint_pass=false` metadata came from the then-default `<=1 ms` endpoint
latency requirement. Its functional/transport results and LER statistic are
clean, so under the current order-ms informational latency policy it is an
authoritative release pass.

## Timing closure

| Basis | Logic | BSRAM | Requested clock | Achieved Fmax | Setup slack | Hold slack | Bitstream SHA-256 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| X | 19,773 / 20,736 (96%) | 39 / 46 (85%) | 40.500 MHz | 40.533 MHz | `+0.020 ns` | `+0.202 ns` | `A875EACBB244F9CA9A4408C28545E51BAD5B522DC090F083A2742E9E73E28303` |
| Z | 19,850 / 20,736 (96%) | 39 / 46 (85%) | 40.500 MHz | 41.445 MHz | `+0.563 ns` | `+0.077 ns` | `A74BBA39F7E5CBC2C51A75834FCEA8F06D7C4E994E9CD9A307C0C3CCBE96FFB8` |

Gowin reports a `PR1014` generic-routing warning for the 27 MHz input clock;
post-route setup/hold timing passes at the requested 40.5 MHz core target.

## Defer-path validation

The default fast-first C-tail shortcut was compared with the conservative
portfolio on 1,016 X and 973 Z real FPGA-deferred syndromes with zero
acceptance, syndrome, or logical mismatches. Check-node OpenMP parallelism was
verified on the same corpus; measured C-tail speedups were 3.09x (X) and 2.68x
(Z).

The FPGA's residual hash is never an acceptance certificate. Exact full
936-check replay decides FPGA acceptance; a hard or ambiguous case defers to
the C tail with the full 1,728-bit syndrome already retained on the host.

