from __future__ import annotations

from pathlib import Path
import os
import shutil
import sys

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from gross144_decoder.paper_gross144_cpu_telescope import (  # noqa: E402
    CpuTelescopeConfig,
    PaperGross144CpuTelescope,
)


RELAY_ROOT = ROOT / "build" / "relay"
PROFILE7_SYNDROME_HEX = (
    "01806180200803000406080605000000000008000000008001140013a92401200040a040"
    "0000008402142130818c00182000809030a0603520808000611028000800001080940008"
    "001000002401010802000830002000040008001000000000802060000000000000000000"
    "0000000000100000000402000000000000120408000000100401120186000400001a0010"
    "060000000000182208042842000000022408000000000000000410802021000000000000"
    "810000000000008020000032040000000000800000000000000000300000000000400000"
)


def _profile7_syndrome() -> np.ndarray:
    return np.unpackbits(
        np.frombuffer(bytes.fromhex(PROFILE7_SYNDROME_HEX), dtype=np.uint8),
        bitorder="little",
    ).astype(np.uint8)


def test_cpu_telescope_config_rejects_invalid_backend() -> None:
    config = CpuTelescopeConfig(backend="bad")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="only the production C CPU telescope"):
        config.validate()


@pytest.mark.skipif(
    os.name != "nt" or shutil.which("wsl.exe") is None or
    not RELAY_ROOT.exists(), reason="production C tail requires Windows + WSL fixture",
)
def test_cpu_telescope_zero_syndrome_is_fast_common_tail_contract() -> None:
    telescope = PaperGross144CpuTelescope(
        RELAY_ROOT, p=0.002, basis="X",
        config=CpuTelescopeConfig(backend="c"),
    )
    result = telescope.decode(np.zeros(1_728, dtype=np.uint8))
    assert result.accepted
    assert result.syndrome_exact
    assert result.stage == "S0"
    assert result.predicted_logicals == (0,) * 12
    assert result.iterations == 0


@pytest.mark.skipif(
    os.name != "nt" or shutil.which("wsl.exe") is None or
    not RELAY_ROOT.exists(), reason="production C tail requires Windows + WSL fixture",
)
def test_cpu_telescope_rejects_non_full_syndrome() -> None:
    telescope = PaperGross144CpuTelescope(
        RELAY_ROOT, p=0.002, basis="Z",
        config=CpuTelescopeConfig(backend="c"),
    )
    with pytest.raises(ValueError, match="full 1,728-bit syndrome"):
        telescope.decode(np.zeros(936, dtype=np.uint8))


@pytest.mark.skipif(
    os.name != "nt" or shutil.which("wsl.exe") is None or
    not RELAY_ROOT.exists(), reason="production C tail requires Windows + WSL fixture",
)
def test_cpu_telescope_profile7_coset_rescue() -> None:
    telescope = PaperGross144CpuTelescope(
        RELAY_ROOT, p=0.002, basis="X",
        config=CpuTelescopeConfig(backend="c"),
    )
    result = telescope.decode(_profile7_syndrome())
    assert result.accepted
    assert result.syndrome_exact
    assert sum(int(value) << index
               for index, value in enumerate(result.predicted_logicals)) == 0x545


@pytest.mark.skipif(
    os.name != "nt" or shutil.which("wsl.exe") is None or
    not RELAY_ROOT.exists(),
    reason="optimized C tail requires Windows + WSL + Relay fixture",
)
def test_cpu_telescope_c_profile7_coset_rescue() -> None:
    telescope = PaperGross144CpuTelescope(
        RELAY_ROOT, p=0.002, basis="X",
        config=CpuTelescopeConfig(backend="c"),
    )
    result = telescope.decode(_profile7_syndrome())
    assert result.accepted
    assert result.syndrome_exact
    assert result.backend == "c_relay"
    assert sum(int(value) << index
               for index, value in enumerate(result.predicted_logicals)) == 0x545
