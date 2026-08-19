# Production architecture

The release path is intentionally narrow:

1. The host samples the pinned Gross144 circuit and packs 936 selected
   detectors into the command `0x31` UART frame.
2. The Tang Nano 20K runs Paper Gross144 S1W in four parallel lanes at the
   timing-clean 40.5 MHz release target.
   Exact board status, cycle count, profile, and 12-bit logical output return
   in one framed response.
3. A terminal FPGA defer retains the full 1,728-bit syndrome on the host and
   invokes one resident C Relay-lite worker. The host never silently switches
   to a different decoder.

The FPGA source closure is the UART top, 40.5 MHz clock, UART protocol, four-lane
schedule/engine/controller, and the compiled Gross144 memories. The host
closure is `paper_gross144.py`, the resident C-tail adapter, protocol framing,
and the board campaign. See [`PRODUCTION_PATH.md`](PRODUCTION_PATH.md) for the
fixed contract and evidence gates.
