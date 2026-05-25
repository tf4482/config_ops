import subprocess
import sys
import tkinter as tk
from tkinter import messagebox

from winutils_python import config as config_loader
from winutils_python import visual


DEFAULT_SECTION = r'''ssh:
  example_set:
    user: 'user'
    host: '192.168.1.1'
    port: 22
    command: 'ls'
'''


def normalize_set_name(set_name: str) -> str:
    return set_name.lstrip("/-")


def choose_ssh_set(config: dict) -> str:
    ssh_sets = config_loader.get_table(config, "ssh")

    if not ssh_sets:
        raise SystemExit("No SSH sets configured in config.yaml. Please add an 'ssh' section.")

    root = tk.Tk()
    root.title("Choose SSH command")
    root.geometry("420x320")
    root.attributes("-topmost", True)

    selected = tk.StringVar(value="")
    tk.Label(root, text="Choose an SSH command set:").pack(padx=12, pady=(12, 6), anchor="w")

    listbox = tk.Listbox(root)
    for name in ssh_sets:
        listbox.insert(tk.END, name)
    listbox.selection_set(0)
    listbox.pack(padx=12, pady=6, fill=tk.BOTH, expand=True)

    def accept_selection() -> None:
        selection = listbox.curselection()
        if not selection:
            messagebox.showwarning("No selection", "Select an SSH command set first.", parent=root)
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
        raise SystemExit("No SSH command set selected")

    return selected.get()


def choose_ssh_set_terminal(config: dict) -> str:
    ssh_sets = config_loader.get_table(config, "ssh")

    if not ssh_sets:
        raise SystemExit("No SSH sets configured in config.yaml. Please add an 'ssh' section.")

    names = list(ssh_sets.keys())
    print("Available SSH sets:")
    for index, name in enumerate(names, start=1):
        print(f"  {index}. {name}")

    while True:
        choice = input("Select SSH set by number or name (or 'exit' to cancel): ").strip()

        if not choice or choice.lower() in {"exit", "quit", "cancel"}:
            raise SystemExit(0)

        normalized = normalize_set_name(choice)
        if normalized in ssh_sets:
            return normalized

        if choice.isdigit():
            number = int(choice)
            if 1 <= number <= len(names):
                return names[number - 1]

        print("Invalid selection. Try again.")


def ssh_set_name(config: dict) -> str:
    if len(sys.argv) > 1:
        return normalize_set_name(sys.argv[1])

    if visual.is_terminal():
        return choose_ssh_set_terminal(config)

    return choose_ssh_set(config)


def get_ssh_set(config: dict, set_name: str) -> dict:
    if set_name.lower() in {"exit", "quit", "cancel"}:
        raise SystemExit(0)

    ssh_sets = config_loader.get_table(config, "ssh")
    ssh_cfg = ssh_sets.get(set_name)

    if not isinstance(ssh_cfg, dict):
        raise ValueError(f"SSH set '{set_name}' was not found in config.yaml")

    return ssh_cfg


def required_str(config: dict, key: str) -> str:
    value = config.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"SSH config value '{key}' must be a non-empty string")

    return value


def required_port(config: dict) -> int:
    value = config.get("port")
    if isinstance(value, int):
        return value

    if isinstance(value, str) and value.isdigit():
        return int(value)

    raise ValueError("SSH config value 'port' must be an integer")


def ensure_section(config: dict) -> None:
    if "ssh" not in config:
        config_loader.append_section_yaml(config, DEFAULT_SECTION)
        visual.print_warning("Added default 'ssh' section to config.yaml. Please configure it before running.")
        raise SystemExit(1)


def main() -> None:
    cfg = config_loader.load(__file__)
    ensure_section(cfg)
    set_name = ssh_set_name(cfg)
    ssh_cfg = get_ssh_set(cfg, set_name)

    user = required_str(ssh_cfg, "user")
    host = required_str(ssh_cfg, "host")
    port = required_port(ssh_cfg)
    command = required_str(ssh_cfg, "command")

    subprocess.run(
        ["ssh", f"{user}@{host}", "-p", str(port), command],
        check=True,
    )


if __name__ == "__main__":
    main()
