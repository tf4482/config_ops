import sys
import tkinter as tk
from tkinter import messagebox

import config_support as config_loader
from winutils_python import connect_smb, file_ops, visual

DEFAULT_SECTION = r'''file_operations:
  example_set:
    smb: false
    robocopy:
      common_options: ['/MT:32', '/W:2', '/R:10', '/XJD', '/XJF', '/XJ', '/XC', '/ETA', '/TEE']
    mirror:
      - source: 'C:\path\to\source'
        target: 'R:\path\to\target'
    copy:
      overwrite: false
      options: ['/E', '/MT:16', '/W:2', '/R:5', '/XJD', '/XJF', '/XJ', '/XC', '/ETA', '/TEE']
      tasks:
        - source: 'C:\path\to\source'
          target: 'E:\path\to\target'
    move:
      overwrite: true
      tasks:
        - source: 'C:\path\to\source'
          target: 'R:\path\to\target'
'''


def normalize_set_name(set_name: str) -> str:
    return set_name.lstrip("/-")


def validate_operation_set_name(config: dict, set_name: str) -> str:
    operation_sets = config_loader.get_table(config, "file_operations")

    if set_name not in operation_sets:
        available_sets = ", ".join(operation_sets) or "none"
        raise SystemExit(f"Unknown file operation set '{set_name}'. Available sets: {available_sets}")

    return set_name


def operation_set_config(config: dict, set_name: str) -> dict:
    operation_sets = config_loader.get_table(config, "file_operations")
    operation_set = operation_sets.get(set_name)

    if not isinstance(operation_set, dict):
        raise TypeError(f"File operation set '{set_name}' must be a table")

    return operation_set


def choose_operation_set(config: dict) -> str:
    operation_sets = config_loader.get_table(config, "file_operations")

    if not operation_sets:
        raise SystemExit("No file operation sets configured in config.yaml. Please add a 'file_operations' section.")

    root = tk.Tk()
    root.title("Choose file operation")
    root.geometry("420x320")
    root.attributes("-topmost", True)

    selected = tk.StringVar(value="")
    label = tk.Label(root, text="Choose a file operation set:")
    label.pack(padx=12, pady=(12, 6), anchor="w")

    listbox = tk.Listbox(root)
    for name in operation_sets:
        listbox.insert(tk.END, name)
    listbox.selection_set(0)
    listbox.pack(padx=12, pady=6, fill=tk.BOTH, expand=True)

    def accept_selection() -> None:
        selection = listbox.curselection()
        if not selection:
            messagebox.showwarning("No selection", "Select an operation set first.", parent=root)
            return

        selected.set(str(listbox.get(selection[0])))
        root.destroy()

    def cancel_selection() -> None:
        root.destroy()

    button_frame = tk.Frame(root)
    button_frame.pack(padx=12, pady=(6, 12), fill=tk.X)
    tk.Button(button_frame, text="Run", command=accept_selection).pack(side=tk.RIGHT, padx=(6, 0))
    tk.Button(button_frame, text="Cancel", command=cancel_selection).pack(side=tk.RIGHT)

    listbox.bind("<Double-Button-1>", lambda _event: accept_selection())
    root.mainloop()

    if not selected.get():
        raise SystemExit("No file operation set selected")

    return selected.get()


def choose_operation_set_terminal(config: dict) -> str:
    operation_sets = config_loader.get_table(config, "file_operations")

    if not operation_sets:
        raise SystemExit("No file operation sets configured in config.yaml. Please add a 'file_operations' section.")

    names = list(operation_sets.keys())
    visual.print_list_header("Available file operation sets:")
    for index, name in enumerate(names, start=1):
        visual.print_list_item(index, name)

    while True:
        choice = input("Select set by number or name (or 'exit' to cancel): ").strip()

        if not choice or choice.lower() in {"exit", "quit", "cancel"}:
            raise SystemExit(0)

        normalized = normalize_set_name(choice)
        if normalized in operation_sets:
            return normalized

        if choice.isdigit():
            number = int(choice)
            if 1 <= number <= len(names):
                return names[number - 1]

        visual.print_warning("Invalid selection. Try again.")


def operation_set_name(config: dict) -> str:
    if len(sys.argv) > 1:
        return validate_operation_set_name(config, normalize_set_name(sys.argv[1]))

    if visual.is_terminal():
        return choose_operation_set_terminal(config)

    return choose_operation_set(config)


def ensure_section(config: dict) -> None:
    if "file_operations" not in config:
        config_loader.append_section_yaml(config, DEFAULT_SECTION)
        visual.print_warning("Added default 'file_operations' section to config.yaml. Please configure it before running.")
        raise SystemExit(1)


def config_for_operation_set_smb(config: dict, set_name: str) -> dict:
    operation_set = operation_set_config(config, set_name)
    set_smb = operation_set.get("smb", False)

    scoped_config = dict(config)

    if set_smb is True:
        if "smb" in config:
            return scoped_config

        scoped_config.pop("smb", None)
        return scoped_config

    if set_smb in (False, None):
        scoped_config.pop("smb", None)
        return scoped_config

    if isinstance(set_smb, dict):
        scoped_config["smb"] = set_smb
        return scoped_config

    raise TypeError(f"File operation set '{set_name}' value 'smb' must be true, false or a table")


def store_prompted_smb_password(config: dict, set_name: str, password: str) -> None:
    operation_set = operation_set_config(config, set_name)
    set_smb = operation_set.get("smb", False)

    if isinstance(set_smb, dict):
        set_smb["encrypted_password"] = connect_smb.encrypt_password(password)
        set_smb.pop("password_file", None)
        set_smb.pop("password", None)
        return

    if set_smb is True:
        config_path = config.get("__config_path__")
        if config_path is None:
            raise ValueError("Loaded configuration is missing internal '__config_path__'")

        config_loader.replace_or_add_string_value(config_path, "smb", "encrypted_password", connect_smb.encrypt_password(password))
        config_loader.remove_value(config_path, "smb", "password_file")
        config_loader.remove_value(config_path, "smb", "password")


def main() -> None:
    config = config_loader.load(__file__)
    ensure_section(config)
    set_name = operation_set_name(config)

    visual.print_start(f"Starting file operations: {set_name}")
    connect_smb.connect_from_config(
        config_for_operation_set_smb(config, set_name),
        on_password_prompted=lambda password: store_prompted_smb_password(config, set_name, password),
    )
    file_ops.run_operation_set(config, set_name)
    visual.print_done(f"File operations finished: {set_name}")


if __name__ == "__main__":
    main()
