from winutils_python import config as config_loader
from winutils_python import connect_smb, visual


DEFAULT_SECTION = r'''smb:
  encrypted_password: ''
  user: ''
  mappings:
    - drive: 'Q:'
      share: '\\SERVER\backup'
    - drive: 'R:'
      share: '\\SERVER\data'
    - drive: 'S:'
      share: '\\SERVER\develop'
'''


def ensure_section(config: dict) -> None:
    if "smb" not in config:
        config_loader.append_section_yaml(config, DEFAULT_SECTION)
        visual.print_warning("Added default 'smb' section to config.yaml. Please configure it before running.")
        raise SystemExit(1)


def main() -> None:
    config = config_loader.load(__file__)
    ensure_section(config)
    connected = connect_smb.connect_from_config(config)

    if connected:
        visual.print_done("SMB shares connected")
    else:
        visual.print_warning("No SMB shares connected")


if __name__ == "__main__":
    main()
