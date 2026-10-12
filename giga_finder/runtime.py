import platform
import subprocess
import webbrowser
from pathlib import Path

from giga_finder.ui import print_info, print_warn

RUNTIME_MODE = None

def select_runtime_mode():
    global RUNTIME_MODE
    system = platform.system()
    if system == "Windows":
        RUNTIME_MODE = "windows"
        print_info("Среда: Windows (автоопределение)")
        return
    if system == "Darwin":
        RUNTIME_MODE = "windows"
        print_info("Среда: macOS (автоопределение)")
        return
    # Linux: может быть нативный или WSL — спрашиваем
    print()
    print("Выберите среду запуска:")
    print("  1. Нативный Linux")
    print("  2. WSL")
    while True:
        choice = input("Среда [1/2]: ").strip()
        if choice == "1":
            RUNTIME_MODE = "windows"
            print_info("Выбрана среда: Нативный Linux")
            return
        if choice == "2":
            RUNTIME_MODE = "wsl"
            print_info("Выбрана среда: WSL")
            return
        print_warn("Введите 1 для нативного Linux или 2 для WSL.")
def open_url(url):
    """URL в браузере: нативно (webbrowser) или через cmd.exe в WSL."""
    if RUNTIME_MODE == "wsl":
        subprocess.run(f'cmd.exe /c start "" "{url}"', shell=True)
    else:
        webbrowser.open(url)
def open_file(path):
    """Локальный файл: нативно (webbrowser) или через wslpath + cmd.exe в WSL."""
    if RUNTIME_MODE == "wsl":
        win_path = subprocess.run(
            ["wslpath", "-w", path],
            capture_output=True, text=True, check=True
        ).stdout.strip()
        subprocess.run(
            ["cmd.exe", "/c", "start", "", win_path],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    else:
        webbrowser.open(Path(path).resolve().as_uri())
