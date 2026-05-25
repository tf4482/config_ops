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
    results = connect_smb.connect_from_config(config)

    if results:
        visual.print_done(f"SMB shares connected: {len(results)} mapping(s)")
    else:
        visual.print_warning("No SMB shares connected")


if __name__ == "__main__":
    main()
