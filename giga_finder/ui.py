import itertools
import sys
import time

from giga_finder.constants import CYAN, GRAY, GREEN, RED, RESET, VERSION, WHITE, YELLOW

def _tag(label, color):
    """Метка вида [INFO]/[WARN]/[ERROR]/[ACTION] с подсветкой (без подсветки вне TTY)."""
    if sys.stdout.isatty():
        return f"{WHITE}[{color}{label}{WHITE}]{RESET}"
    return f"[{label}]"


def colorize(text, color):
    return f"{color}{text}{RESET}" if sys.stdout.isatty() else text


def print_hint(message="? — подсказка"):
    print(colorize(message, GRAY))


def is_help_request(value):
    return (value or "").strip().lower() in ("?", "help")


def _wrap_help_line(text, width):
    if text == "":
        return [""]
    rows = []
    current = ""
    for word in text.split():
        trial = word if not current else f"{current} {word}"
        if len(trial) <= width:
            current = trial
        else:
            if current:
                rows.append(current)
            current = word
    if current:
        rows.append(current)
    return rows or [""]


def render_help_box(title, lines, width=68):
    """Рамка подсказки. Цвет добавляет print_help_box."""
    wrapped = []
    for line in lines:
        wrapped.extend(_wrap_help_line(line, width))
    inner = max([len(title) + 2, width] + [len(line) for line in wrapped])
    dashes = max(1, inner - len(title) - 1)
    top = "┌─ " + title + " " + "─" * dashes + "┐"
    box_width = len(top) - 4
    rows = [top]
    for line in wrapped:
        rows.append("│ " + line.ljust(box_width) + " │")
    rows.append("└" + "─" * (len(top) - 2) + "┘")
    return "\n".join(rows)


def print_help_box(title, lines):
    print(colorize(render_help_box(title, lines), YELLOW))


def print_divider():
    print("\n" + "=" * 50)


def print_info(message):
    print(f"{_tag('INFO', GREEN)} {message}")


def print_warn(message):
    print_divider()
    print(f"{_tag('WARN', YELLOW)} {message}")


def print_error(message):
    print_divider()
    print(f"{_tag('ERROR', RED)} {message}")


def print_action(message, end="\n"):
    print_divider()
    print(f"{_tag('ACTION', CYAN)} {message}", end=end, flush=True)


def action_label():
    """Подсветка '[ACTION]' для промптов ввода — как у print_action."""
    return _tag("ACTION", CYAN)

# --- Анимация спиннера ---
def animate_spinner(stop_event):
    spinner = itertools.cycle(["\\", "|", "/", "-"])
    while not stop_event.is_set():
        sys.stdout.write(next(spinner) + '\b')
        sys.stdout.flush()
        time.sleep(0.1)

# --- Ввод с таймаутом ---
# Пауза после последнего символа (без Enter), после которой ввод считается завершённым.
_INPUT_SETTLE_SECONDS = 2.0


def input_with_timeout(prompt, timeout_seconds):
    """Ввод строки с таймаутом; None при истечении.

    Единое правило завершения для Windows и Unix:
    - до первого символа действует полный таймаут бездействия
      (любая клавиша/поступление данных сбрасывает отсчёт);
    - после ввода строка завершается переносом строки (хвост 0.3 с на
      случай \\r\\n) либо паузой _INPUT_SETTLE_SECONDS после последнего
      символа, если Enter не было (медленная печать, вставка без
      завершающего переноса)."""
    print(prompt, end='', flush=True)
    if sys.platform == "win32":
        import msvcrt
        parts = []
        buf = bytearray()
        last_was_newline = False
        idle_since = time.time()
        while True:
            if msvcrt.kbhit():
                # getwche — С ЭХО: пользователь видит то, что вставляет/печатает.
                ch = msvcrt.getwche()
                if ch == "\x03":  # Ctrl-C
                    print()
                    raise KeyboardInterrupt
                if ch in ("\r", "\n"):
                    # Двойное окончание строки (\r\n из буфера обмена) — считаем за одно.
                    if not last_was_newline:
                        parts.append(bytes(buf).decode("utf-8", errors="replace"))
                        buf.clear()
                    last_was_newline = True
                else:
                    buf += ch.encode("utf-8")
                    last_was_newline = False
                idle_since = time.time()
                continue
            idle = time.time() - idle_since
            if not (parts or buf):
                # До первого символа — полный таймаут бездействия.
                if idle > timeout_seconds:
                    print()
                    return None
            elif idle > (0.3 if last_was_newline else _INPUT_SETTLE_SECONDS):
                # Перенос строки получен (хвост 0.3 с) либо пауза без Enter —
                # ввод завершён (медленная печать/вставка без переноса).
                break
            time.sleep(0.02)
        if buf:
            parts.append(bytes(buf).decode("utf-8", errors="replace"))
        print()
        text = "\n".join(parts).strip()
        return text if text else None
    else:
        import os, select
        fd = sys.stdin.fileno()
        # cbreak-режим — только для реального терминала; при пайпе/файле читаем как есть
        use_cbreak = False
        old_settings = None
        try:
            import termios, tty
            old_settings = termios.tcgetattr(fd)
            # cbreak для построчного чтения с таймаутом; ЭХО оставляем включённым —
            # пользователь должен видеть то, что вставляет.
            tty.setcbreak(fd)
            use_cbreak = True
        except (ImportError, termios.error, OSError, AttributeError):
            pass
        try:
            chunks = []
            got_data = False
            ended_with_newline = False
            idle_since = time.time()
            while True:
                idle = time.time() - idle_since
                if not got_data:
                    # До первых данных — полный таймаут бездействия.
                    if idle > timeout_seconds:
                        break
                    wait = min(timeout_seconds - idle, 0.15)
                else:
                    # После ввода: хвост 0.3 с после переноса строки либо
                    # пауза _INPUT_SETTLE_SECONDS без переноса — ввод завершён.
                    limit = 0.3 if ended_with_newline else _INPUT_SETTLE_SECONDS
                    if idle > limit:
                        break
                    wait = min(limit - idle, 0.15)
                ready, _, _ = select.select([fd], [], [], max(wait, 0.01))
                if not ready:
                    continue
                chunk = os.read(fd, 4096)
                if not chunk:
                    break
                chunks.append(chunk)
                got_data = True
                ended_with_newline = chunk[-1:] in (b"\n", b"\r")
                idle_since = time.time()
            text = b"".join(chunks).decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n").strip()
            print()
            return text if text else None
        finally:
            if use_cbreak and old_settings is not None:
                termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

# --- Логотип GigaFinder (ASCII-арт) ---
_LOGO_LINES = [
    "  ██████╗ ██╗ ██████╗  █████╗ ███████╗██╗███╗   ██╗██████╗ ███████╗██████╗ ",
    "██╔════╝ ██║██╔════╝ ██╔══██╗██╔════╝██║████╗  ██║██╔══██╗██╔════╝██╔══██╗",
    "██║  ███╗██║██║  ███╗███████║█████╗  ██║██╔██╗ ██║██║  ██║█████╗  ██████╔╝",
    "██║   ██║██║██║   ██║██╔══██║██╔══╝  ██║██║╚██╗██║██║  ██║██╔══╝  ██╔══██╗",
    "╚██████╔╝██║╚██████╔╝██║  ██║██║     ██║██║ ╚████║██████╔╝███████╗██║  ██║",
    " ╚═════╝ ╚═╝ ╚═════╝ ╚═╝  ╚═╝╚═╝     ╚═╝╚═╝  ╚═══╝╚═════╝ ╚══════╝╚═╝  ╚═╝",
]


def print_logo():
    max_len = max(len(line) for line in _LOGO_LINES)
    border = "=" * (max_len + 4)
    print(border)
    for line in _LOGO_LINES:
        print(f"|| {line.ljust(max_len)}||")
    print(border)
    print(f"  Версия: {VERSION}")
