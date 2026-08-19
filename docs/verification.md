# Verification

The release checks are deliberately tied to the production closure:

- `make test-python`: retained Python/reference suite;
- `make test-rtl`: Icarus compile of the four-lane UART top;
- `make lint`: Verilator lint of the same source closure;
- `make build-board`: Gowin production X or Z bitstream;
- `make smoke` / `make proof`: live COM6 board campaign with C-only defer.

The current checked-in baseline is 58 passing Python tests, 16 skipped
fixture-dependent tests, 12 RTL subtests, and a successful Icarus UART-top
compile plus the forced-error recovery regression. Gowin P&R and large-shot
board proof remain hardware operations, not claims inferred from simulation.
