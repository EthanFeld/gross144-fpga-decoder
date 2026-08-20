# Verification

Verification is split into portable checks, fixture-backed reference checks,
and hardware-only release checks.

## Portable checks

```bash
python -m pip install -r requirements.txt
python -m pytest tests -q
make test-rtl-recovery
make lint
```

`pytest` intentionally skips tests that require the external Relay-BP fixture
when `build/relay` is absent. Icarus Verilog and Verilator are required for the
RTL targets.

GitHub Actions runs the portable Python suite on Python 3.11 and 3.12, checks
the pinned Relay fixture contract, compiles/runs the RTL recovery regression,
and lints the production RTL closure.

## Pinned Relay-BP fixture

The public Relay-BP fixture is not vendored. Bootstrap the exact checkout used
by this project with:

```bash
python tools/bootstrap_relay.py
```

The script fetches commit
`19d7023d476248858fc01bdf087ce673feaa4ef4` into ignored `build/relay` and
verifies the SHA-256 of all four Gross144 Stim fixtures used by the models.
Then rerun `pytest` to enable fixture-dependent tests.

## Hardware release checks

These require Windows 11, Gowin EDA, WSL2 with GCC/OpenMP, and a Tang Nano 20K:

```powershell
powershell -ExecutionPolicy Bypass -File tools\build_board.ps1 -Basis X
powershell -ExecutionPolicy Bypass -File tools\flash_gowin.ps1 -Basis X -Mode sram
powershell -ExecutionPolicy Bypass -File tools\run_board_proof.ps1 -Port COM6 -Basis X -Shots 300000
```

Repeat for basis `Z`. The build rejects negative post-route setup/hold slack.
The board campaign treats parser, transport, decoder, and wrong-logical events
as endpoint failures.

The authoritative release result is the final physical-board evidence in
[`reports/HEADLINE_BENCHMARKS.md`](../reports/HEADLINE_BENCHMARKS.md), with
machine-readable provenance in
[`reports/release_evidence.json`](../reports/release_evidence.json). Simulation
or CI results are not substituted for that board evidence.
