import subprocess
import sys
import winreg
from pathlib import Path

LED_PC_ON_URL = "https://www.virtualsmarthome.xyz/url_routine_trigger/activate.php?trigger=e"
BOX_PC_ON_URL = "https://www.virtualsmarthome.xyz/url_routine_trigger/activate.php?trigger=a"
LED_PC_OFF_URL = "https://www.virtualsmarthome.xyz/url_routine_trigger/activate.php?trigger=b"
BOX_PC_OFF_URL = "https://www.virtualsmarthome.xyz/url_routine_trigger/activate.php?trigger=c"
REGISTRY_PATH = r"Software\peripherals"


def script_dir() -> Path:
    return Path(__file__).resolve().parent


def read_device_state(device: str) -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REGISTRY_PATH) as key:
            value, _ = winreg.QueryValueEx(key, device)
            return bool(value)
    except FileNotFoundError:
        return False


def write_device_state(device: str, enabled: bool) -> None:
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, REGISTRY_PATH) as key:
        winreg.SetValueEx(key, device, 0, winreg.REG_DWORD, 1 if enabled else 0)


def trigger_url(url: str) -> None:
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0

    subprocess.Popen(
        ["curl.exe", url],
        cwd=script_dir(),
        creationflags=subprocess.CREATE_NO_WINDOW,
        startupinfo=startupinfo,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def delete_state_files() -> None:
    write_device_state("led", False)
    write_device_state("box", False)


def create_state_files() -> None:
    write_device_state("led", True)
    write_device_state("box", True)


def device_on_url(device: str) -> str:
    if device == "led":
        return LED_PC_ON_URL

    return BOX_PC_ON_URL


def device_off_url(device: str) -> str:
    if device == "led":
        return LED_PC_OFF_URL

    return BOX_PC_OFF_URL


def turn_on() -> None:
    delete_state_files()
    create_state_files()
    trigger_url(LED_PC_ON_URL)
    trigger_url(BOX_PC_ON_URL)


def turn_off() -> None:
    delete_state_files()
    trigger_url(LED_PC_OFF_URL)
    trigger_url(BOX_PC_OFF_URL)


def toggle_all_devices() -> None:
    if read_device_state("led") or read_device_state("box"):
        turn_off()
        return

    turn_on()


def suspend() -> None:
    trigger_url(LED_PC_OFF_URL)
    trigger_url(BOX_PC_OFF_URL)


def resume() -> None:
    resumed_any_device = False

    if read_device_state("led"):
        trigger_url(LED_PC_ON_URL)
        resumed_any_device = True

    if read_device_state("box"):
        trigger_url(BOX_PC_ON_URL)
        resumed_any_device = True

    if not resumed_any_device:
        trigger_url(LED_PC_OFF_URL)
        trigger_url(BOX_PC_OFF_URL)


def turn_device_on(device: str) -> None:
    write_device_state(device, True)
    trigger_url(device_on_url(device))


def turn_device_off(device: str) -> None:
    write_device_state(device, False)
    trigger_url(device_off_url(device))


def toggle_device(device: str) -> None:
    if read_device_state(device):
        turn_device_off(device)
        return

    turn_device_on(device)


def suspend_device(device: str) -> None:
    trigger_url(device_off_url(device))


def resume_device(device: str) -> None:
    if read_device_state(device):
        trigger_url(device_on_url(device))
        return

    trigger_url(device_off_url(device))


def normalize_argument(argument: str) -> str:
    return argument.strip().lower().lstrip("/-")


def run_device_command(device: str, command: str) -> None:
    if command == "on":
        turn_device_on(device)
    elif command == "off":
        turn_device_off(device)
    elif command == "suspend":
        suspend_device(device)
    elif command == "resume":
        resume_device(device)


def run_all_devices_command(command: str) -> None:
    if command == "on":
        turn_on()
    elif command == "off":
        turn_off()
    elif command == "suspend":
        suspend()
    elif command == "resume":
        resume()


def main() -> None:
    arguments = [normalize_argument(argument) for argument in sys.argv[1:]]

    if not arguments:
        toggle_all_devices()
        return

    if arguments[0] in ("led", "boxen"):
        device = "box" if arguments[0] == "boxen" else arguments[0]
        if len(arguments) < 2:
            toggle_device(device)
            return

        command = arguments[1]
        run_device_command(device, command)
        return

    run_all_devices_command(arguments[0])


if __name__ == "__main__":
    main()
