from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

from config_ops.config import DEFAULT_CONFIG, parse_config
from config_ops.configfile import ConfigError


# Valid document: the template with its placeholder replaced
def config_document() -> dict[str, Any]:
    document = yaml.safe_load(DEFAULT_CONFIG.replace("CHANGE_ME", "secret"))
    return copy.deepcopy(document)


def test_template_is_rejected_until_edited() -> None:
    with pytest.raises(ConfigError, match=r"edit placeholder value at smb\.user"):
        parse_config(yaml.safe_load(DEFAULT_CONFIG))


def test_example_file_matches_embedded_template() -> None:
    example = Path(__file__).resolve().parents[1] / "config.example.yml"

    assert example.read_text(encoding="utf-8") == DEFAULT_CONFIG


def test_smb_user_is_optional() -> None:
    document = config_document()
    del document["smb"]["user"]

    config = parse_config(document)

    assert config.smb is not None and config.smb.user is None


def test_valid_document_is_parsed() -> None:
    config = parse_config(config_document())

    assert config.smb is not None and config.smb.mappings[1].drive == "R:"
    assert config.files["backup"].tasks[1].overwrite is False
    assert config.dates["screenshots"].change_files_in_place is True


def empty_password_file(document: dict[str, Any]) -> None:
    document["smb"]["password_file"] = ""


def add_unknown_root_key(document: dict[str, Any]) -> None:
    document["unknown"] = {}


def drop_ssh_host(document: dict[str, Any]) -> None:
    del document["ssh"]["remote_backup"]["host"]


def bool_ssh_port(document: dict[str, Any]) -> None:
    document["ssh"]["remote_backup"]["port"] = True


def unknown_file_kind(document: dict[str, Any]) -> None:
    document["files"]["backup"]["tasks"][0]["type"] = "delete"


def extension_without_dot(document: dict[str, Any]) -> None:
    document["archive"]["photos"]["extensions"] = ["jpg"]


def pattern_without_groups(document: dict[str, Any]) -> None:
    document["dates"]["screenshots"]["patterns"] = ["^(?P<year>\\d{4})"]


def smb_flag_without_section(document: dict[str, Any]) -> None:
    del document["smb"]
    document["files"]["backup"]["smb"] = True


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (empty_password_file, r"smb\.password_file must be a non-empty string"),
        (add_unknown_root_key, r"configuration keys are invalid \(unknown: unknown\)"),
        (drop_ssh_host, r"ssh\.remote_backup keys are invalid \(missing: host\)"),
        (bool_ssh_port, r"ssh\.remote_backup\.port must be an integer from 1 to 65535"),
        (unknown_file_kind, r"files\.backup\.tasks\[0\]\.type must be one of"),
        (extension_without_dot, r"archive\.photos\.extensions must contain extensions"),
        (pattern_without_groups, r"dates\.screenshots\.patterns needs named groups"),
        (smb_flag_without_section, r"files\.backup\.smb requires the smb section"),
    ],
)
def test_invalid_documents_are_rejected(change: Any, message: str) -> None:
    document = config_document()
    change(document)

    with pytest.raises(ConfigError, match=message):
        parse_config(document)


def test_ssh_timeout_is_capped_at_one_day() -> None:
    document = config_document()
    document["ssh"]["remote_backup"]["timeout_seconds"] = 10**6

    assert parse_config(document).ssh["remote_backup"].timeout_seconds == 86400.0
