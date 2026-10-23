from __future__ import annotations

import subprocess
from unittest.mock import Mock

import pytest

from config_ops import peripherals
from config_ops.config import Device, PeripheralSettings

LED = Device("led", "https://on.example", "https://off.example")
SETTINGS = PeripheralSettings(r"Software\config_ops", {"led": LED})


def test_state_changes_only_after_success(monkeypatch: pytest.MonkeyPatch) -> None:
    trigger = Mock(side_effect=subprocess.CalledProcessError(22, ["curl.exe"]))
    write_state = Mock()
    monkeypatch.setattr(peripherals, "trigger_url", trigger)
    monkeypatch.setattr(peripherals, "write_state", write_state)

    with pytest.raises(subprocess.CalledProcessError):
        peripherals.run_command(SETTINGS.registry_path, LED, "on")

    write_state.assert_not_called()


def test_words_select_devices_and_command() -> None:
    assert peripherals.parse_words(SETTINGS, ["/LED", "off"]) == ([LED], "off")
    assert peripherals.parse_words(SETTINGS, []) == ([LED], "toggle")
    with pytest.raises(peripherals.PeripheralsError, match="invalid argument"):
        peripherals.parse_words(SETTINGS, ["tv"])
