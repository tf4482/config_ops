import config_support as config_loader
from winutils_python import connect_smb, visual


DEFAULT_SECTION = r'''smb:
  # Set this to the Windows/SMB account used for the mappings below.
  # Examples: 'DOMAIN\user', 'SERVER\user' or '.\local_user'
  user: 'DOMAIN\user'
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
        visual.print_warning(
            "Added default 'smb' section to config.yaml. "
            "Please configure 'smb.user' and 'smb.mappings' before running. "
            "The encrypted password will be added after the first successful password prompt."
        )
        raise SystemExit(1)


def store_prompted_smb_password(config: dict, password: str) -> None:
    config_path = config.get("__config_path__")
    if config_path is None:
        raise ValueError("Loaded configuration is missing internal '__config_path__'")

    config_loader.replace_or_add_string_value(config_path, "smb", "encrypted_password", connect_smb.encrypt_password(password))
    config_loader.remove_value(config_path, "smb", "password_file")
    config_loader.remove_value(config_path, "smb", "password")


def main() -> None:
    config = config_loader.load(__file__)
    ensure_section(config)
    results = connect_smb.connect_from_config(
        config,
        on_password_prompted=lambda password: store_prompted_smb_password(config, password),
    )

    if results:
        visual.print_done(f"SMB shares connected: {len(results)} mapping(s)")
    else:
        visual.print_warning("No SMB shares connected")


if __name__ == "__main__":
    main()
