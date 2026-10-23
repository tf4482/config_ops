from __future__ import annotations

import stat
import sys
from pathlib import Path

import pytest

from config_ops.config import DEFAULT_CONFIG, parse_config
from config_ops.configfile import ConfigCreated, ConfigError, load_config

VALID = DEFAULT_CONFIG.replace("CHANGE_ME", "secret")


# Executable directory and a home with a user configuration
def locations(tmp_path: Path) -> tuple[Path, Path]:
    beside = tmp_path / "bin"
    home = tmp_path / "home"
    beside.mkdir()
    (home / ".config" / "config_ops").mkdir(parents=True)
    return beside, home


def test_config_beside_executable_takes_precedence(tmp_path: Path) -> None:
    beside, home = locations(tmp_path)
    (beside / "config.yml").write_text(VALID, encoding="utf-8")
    (home / ".config" / "config_ops" / "config.yml").write_text("ssh: {}\n", encoding="utf-8")

    path, config = load_config(DEFAULT_CONFIG, parse_config, executable_dir=beside, home=home)

    assert path == beside / "config.yml"
    assert config.smb is not None


def test_invalid_config_does_not_fall_back_or_reveal_content(tmp_path: Path) -> None:
    beside, home = locations(tmp_path)
    (beside / "config.yml").write_text("smb: [unclosed secret-token\n", encoding="utf-8")
    (home / ".config" / "config_ops" / "config.yml").write_text(VALID, encoding="utf-8")

    with pytest.raises(ConfigError) as caught:
        load_config(DEFAULT_CONFIG, parse_config, executable_dir=beside, home=home)

    assert "secret-token" not in str(caught.value)


def test_first_run_creates_template_that_needs_editing(tmp_path: Path) -> None:
    with pytest.raises(ConfigCreated) as created:
        load_config(DEFAULT_CONFIG, parse_config, executable_dir=tmp_path / "bin", home=tmp_path)

    with pytest.raises(ConfigError, match="edit placeholder value"):
        load_config(DEFAULT_CONFIG, parse_config, executable_dir=tmp_path / "bin", home=tmp_path)
    if sys.platform != "win32":
        assert stat.S_IMODE(created.value.path.stat().st_mode) == 0o600
