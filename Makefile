PYTHON ?= python
PORT ?= COM6
BASIS ?= X
SHOTS ?= 20000

.PHONY: test-python test-rtl test-rtl-recovery lint build-board flash-board smoke proof profile clean

test-python:
	$(PYTHON) -m pytest tests -q

test-rtl:
	$(PYTHON) tools/run_iverilog.py --output build/sim/paper_s1w_uart.vvp --top tb_tang_nano_20k_paper_s1w_four_lane_uart_top --filelist tools/rtl_s1w_four_lane_uart_top.f

test-rtl-recovery: test-rtl
	vvp build/sim/paper_s1w_uart.vvp +ZERO_SYNDROME +ERROR_RECOVERY +EXPECTED_LOGICAL=0 +EXPECTED_SWEEPS=0 +EXPECTED_STATUS=1 +EXPECTED_BASIS=0

lint:
	verilator --lint-only --language 1800-2012 -DGROSS144_SIM --top-module tang_nano_20k_paper_s1w_four_lane_uart_fast_51_top -f tools/rtl_s1w_four_lane_uart_top.f

build-board:
	powershell -ExecutionPolicy Bypass -File tools/build_board.ps1 -Basis $(BASIS)

flash-board:
	powershell -ExecutionPolicy Bypass -File tools/flash_gowin.ps1 -Basis $(BASIS) -Mode sram

smoke:
	powershell -ExecutionPolicy Bypass -File tools/run_board_proof.ps1 -Port $(PORT) -Basis $(BASIS) -Shots 100 -Smoke

proof:
	powershell -ExecutionPolicy Bypass -File tools/run_board_proof.ps1 -Port $(PORT) -Basis $(BASIS) -Shots $(SHOTS)

profile:
	$(PYTHON) tools/profile_c_tail.py --basis both --shots 1000 --set-iterations 50 --output build/c_tail_profile.json

clean:
	$(PYTHON) -c "from pathlib import Path; p=Path('build/sim'); p.mkdir(parents=True, exist_ok=True); [x.unlink() for x in p.glob('*.vvp') if x.is_file()]"
