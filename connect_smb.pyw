"""Connect SMB network shares defined in the project config file.

This GUI-friendly entry script loads the top-level ``smb`` configuration,
delegates mapping work to ``winutils_python.connect_smb``, and persists prompted
passwords in obfuscated form for later runs.
"""

from typing import Any

from winutils_python import config as config_utils
from winutils_python import connect_smb, visual

CONFIG_SECTION = "smb"


DEFAULT_SECTION = r'''smb:
  user: 'DOMAIN\user'
  mappings:
    - drive: 'Q:'
      share: '\\SERVER\backup'
    - drive: 'R:'
      share: '\\SERVER\data'
    - drive: 'S:'
      share: '\\SERVER\develop'
'''

def ensure_section(config: dict[str, Any]) -> None:
    """Ensure the SMB section exists and has table shape."""

    if CONFIG_SECTION not in config:
        config_utils.append_section_yaml(config, DEFAULT_SECTION)
        visual.print_warning(
            f"Added default '{CONFIG_SECTION}' section to config.yaml. "
            "Please configure 'smb.user' and 'smb.mappings' before running. "
            "The encrypted password will be added after the first successful password prompt."
        )
        raise SystemExit(1)

    config_utils.get_table(config, CONFIG_SECTION)


def main() -> None:
    """Connect all configured top-level SMB mappings."""

    config = config_utils.load(__file__)
    ensure_section(config)
    results = connect_smb.connect_from_config(
        config,
        on_password_prompted=lambda password: connect_smb.store_prompted_password(config, password),
    )

    if results:
        visual.print_done(f"SMB shares connected: {len(results)} mapping(s)")
    else:
        visual.print_warning("No SMB shares connected")


if __name__ == "__main__":
    main()
