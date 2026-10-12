#!/usr/bin/env python3
"""GigaFinder — поиск стендов Giga Cowork и управление пространствами."""

import itertools
import json
import re
import platform
import subprocess
import sys
import threading
import time
import zipfile
import zipfile
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
# --- Runtime mode: Windows / WSL ---
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

# --- Версия ---
VERSION = "v4.2.0"
# Единый таймаут ожидания ввода для всех меню (секунды)
INPUT_TIMEOUT = 300

# --- Пути и константы ---
REPO_PATH = Path.home() / "Documents/Giga/cowork/argocd"
REPO_LOCAL_FILE = Path(__file__).parent / "repo_local_path.txt"  # вручную указанный путь к репозиторию
REPO_URL = "git@git.sberdevices.ru:gigabb/infra/cowork/argocd.git"
DSN_CACHE_PATH = Path.home() / ".gigafinder" / "dsn-cache.json"
FAQ_AGENT_URL = "https://cowork.gigab2b.ru/workspaces/c63c8b9f-bdf0-4805-a314-b8da3eaeaea7"
CONFIG = "config.yaml"
ZONES_DIR = Path(__file__).parent / "zones"
ZONES_CONFIG_FILE = ZONES_DIR / "zones_config.json"
EXEC_DIR = Path(__file__).parent / "exec"
ACCOUNTS_DIR = Path(__file__).parent / "accounts"  # сюда складываются Excel с email/password (пункт 9)
REPO_CHECK_INTERVAL = 15 * 60  # фоновая проверка изменений в репозитории раз в 15 минут
REPO_FAST_CHECK_INTERVAL = 30   # ускоренная проверка, когда накопилось много изменений
REPO_FAST_CHECK_THRESHOLD = 10  # порог: больше N коммитов — не ждём полного интервала

# Зоны подменю «Управление пространствами»: code — суффикс ArgoCD, cluster — имя кластера в репозитории.
WORKSPACE_ZONES = [
    {"code": "st02", "cluster": "cce-prod-st02"},
    {"code": "tb01", "cluster": "cce-prod-tb01"},
    {"code": "sdb-tb01", "cluster": "cce-sdb-tb01-stands"},
    {"code": "tb04", "cluster": "cce-prod-tb04"},
]
# Нестандартные зоны: путь не по шаблону cce-prod/cowork/clusters/<cluster>; label — stand_dir в результатах.
EXTRA_ZONES = [
    {
        "path": "cce-sdb-tb01/cowork/stands",
        "label": "cce-sdb-tb01-stands",
        "url": "https://argocd.cowork.gigab2b.ru/applications/cowork/{folder}?resource=",
        "stand_prefixes": ("sber-", "cowork"),
        "app_template": "cowork/{stand_name}-{app_suffix}",
        "argocd_base": "https://argocd.cowork.gigab2b.ru",
        "links_app": "cowork-heimdall",
    },
]
# Белый список: finder читает в репозитории только эти корневые папки.
ALLOWED_REPO_DIRS = ("cce-prod", "cce-prod-tb01", "cce-sdb-tb01")
def _zone_path_allowed(path):
    """Проверка, что путь зоны лежит внутри одной из ALLOWED_REPO_DIRS."""
    top = path.split("/", 1)[0]
    return top in ALLOWED_REPO_DIRS
# Приложение ArgoCD с управляющим подом debug-ubuntu.
ARGOCD_APP = "cowork/cowork"
DEBUG_POD_PREFIX = "debug-ubuntu"

# --- Базовые пространства (только стенд demo) ---
BASE_WORKSPACE_IDS = [
    "f7a1d8cc-63d8-4063-a9b0-345cad57b5d6",
    "f4d1b8a9-12b2-4cd8-af5c-3d03b459d24e",
    "81782a49-c2a8-4c8a-8b06-03c1c61201b0",
    "5d261c97-8979-41b5-9970-b6185518f6d0",
    "ede5105a-721f-46cc-a529-462353a45855",
    "f77ce42b-9f5f-4870-a5b4-c46a0e6072c4",
    "9d5999da-fbcb-4278-ad9b-a5c90e686f69",
]
DEMO_STAND_SUBDOMAIN = "demo"
# DSN стенда demo — опция работает без ввода DSN.
DEMO_YGGDRASIL_URL = "postgresql://yggdrasil:22C3LZ44HU0NBvlLj32aQ7IA4S71nmpqLpa9RA0zBIvMj8Bg@yggdrasil-postgres-rw.cowork-st02-0503.svc.cluster.local:5432/yggdrasil"
DEMO_HEIMDALL_URL = "postgresql://heimdall_auth:8Kmq71GyU1ezPmkys8mczPHbh62pxhMnsWmHi0efSmfuDEPR@heimdall-auth-postgres-rw.cowork-st02-0503.svc.cluster.local:5432/heimdall_auth"

# --- Цветовые коды ---
WHITE = '\033[97m'
GREEN = '\033[92m'
YELLOW = '\033[93m'
RED = '\033[91m'
CYAN = '\033[96m'
MAGENTA = '\033[95m'  # фиолетовый
RESET = '\033[0m'

# --- Цветной вывод ---
def _tag(label, color):
    """Метка вида [INFO]/[WARN]/[ERROR]/[ACTION] с подсветкой (без подсветки вне TTY)."""
    if sys.stdout.isatty():
        return f"{WHITE}[{color}{label}{WHITE}]{RESET}"
    return f"[{label}]"


def colorize(text, color):
    return f"{color}{text}{RESET}" if sys.stdout.isatty() else text


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

# --- Конфигурация зон ---
class ZoneManager:
    """Зоны Finder: конфигурация zones_config.json + синхронизация с репозиторием."""

    def __init__(self, config_file):
        self.config_file = Path(config_file)


    def load_zones(self):
        if not self.config_file.exists():
            return []
        try:
            zones = json.loads(self.config_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        if not isinstance(zones, list):
            return []
        # Защита: из конфига берём только зоны в допустимых корневых папках.
        zones = [z for z in zones if isinstance(z, dict) and _zone_path_allowed(z.get("path", ""))]
        # JSON хранит кортежи как списки — приводим stand_prefixes к tuple.
        for zone in zones:
            if "stand_prefixes" in zone:
                zone["stand_prefixes"] = tuple(zone["stand_prefixes"])
        # Автокоррекция URL, если ранее был создан файл с багом.
        corrected = False
        for zone in zones:
            if "url" in zone and "argocd-cce-prod-" in zone["url"]:
                code = zone["path"].split("/")[-1].rsplit("-", 1)[-1]
                zone["url"] = f"https://argocd-{code}.cowork.ru/applications/cowork/{{folder}}?resource="
                corrected = True
        if corrected:
            self.save_zones(zones)
        return zones

    def save_zones(self, zones):
        ZONES_DIR.mkdir(parents=True, exist_ok=True)
        self.config_file.write_text(json.dumps(zones, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def make_zone_from_cluster(cluster_name):
        # cluster_name: "cce-prod-st02"
        code = cluster_name.rsplit("-", 1)[-1]  # st02, tb01, tb04
        return {
            "path": f"cce-prod/cowork/clusters/{cluster_name}",
            "label": cluster_name,
            "url": f"https://argocd-{code}.cowork.ru/applications/cowork/{{folder}}?resource="
        }

    @staticmethod
    def get_remote_clusters(repo_root):
        clusters_path = repo_root / "cce-prod/cowork/clusters"
        if not clusters_path.exists():
            return []
        return sorted(d.name for d in clusters_path.iterdir() if d.is_dir())

    def discover_and_save_zones(self, repo_root):
        zones = [self.make_zone_from_cluster(c) for c in self.get_remote_clusters(repo_root)]
        zones += [dict(z) for z in EXTRA_ZONES if _zone_path_allowed(z["path"])]
        self.save_zones(zones)
        return zones

    def check_and_update_zones(self, repo_root, zones):
        remote_clusters = self.get_remote_clusters(repo_root)
        # Устаревшие зоны (больше не актуальны): убираем из конфига до проверки доступности.
        allowed_paths = {f"cce-prod/cowork/clusters/{c}" for c in remote_clusters}
        allowed_paths |= {z["path"] for z in EXTRA_ZONES}
        stale = [z for z in zones if z["path"] not in allowed_paths]
        if stale:
            zones[:] = [z for z in zones if z["path"] in allowed_paths]
            self.save_zones(zones)
            for z in stale:
                print_info(f"Удалена устаревшая зона из конфигурации: {z['label']}.")
        known = {z["path"].split("/")[-1] for z in zones}
        for zone in zones:
            if zone["path"].startswith("cce-prod/cowork/clusters/"):
                if zone["path"].split("/")[-1] not in remote_clusters:
                    print_error(f"Кластер {zone['label']} не доступен.")
            elif not (repo_root / zone["path"]).exists():
                print_error(f"Зона {zone['label']} не доступна (папка {zone['path']}).")
        # Нестандартные зоны: добавляем в конфиг, если их ещё нет,
        # либо синхронизируем поля (например, stand_prefixes), если они изменились.
        known_paths = {z["path"] for z in zones}
        for extra in EXTRA_ZONES:
            if not _zone_path_allowed(extra["path"]):
                print_error(f"Зона {extra['label']} пропущена: путь вне {ALLOWED_REPO_DIRS}.")
                continue
            if extra["path"] not in known_paths:
                zones.append(dict(extra))
                self.save_zones(zones)
                print_info(f"Добавлена зона: {extra['label']} ({extra['path']}).")
            else:
                for zone in zones:
                    if zone["path"] != extra["path"]:
                        continue
                    # JSON хранит кортежи как списки — нормализуем перед сравнением.
                    norm_zone = dict(zone)
                    norm_extra = dict(extra)
                    for key in ("stand_prefixes",):
                        if key in norm_zone:
                            norm_zone[key] = tuple(norm_zone[key])
                        if key in norm_extra:
                            norm_extra[key] = tuple(norm_extra[key])
                    if norm_zone != norm_extra:
                        zones[zones.index(zone)] = dict(extra)
                        self.save_zones(zones)
                        print_info(f"Обновлена зона: {extra['label']} ({extra['path']}).")
                    break

        updated = False
        for cluster in (c for c in remote_clusters if c not in known):
            print_warn(f"Обнаружен новый кластер: {cluster}")
            answer = input_with_timeout("Добавить в конфигурацию Finder новый кластер? (yes/no): ", INPUT_TIMEOUT)
            if answer and answer.lower() in ("y", "yes"):
                zones.append(self.make_zone_from_cluster(cluster))
                self.save_zones(zones)
                print_info(f"Кластер {cluster} добавлен в конфигурацию.")
                updated = True
            else:
                print_info(f"Кластер {cluster} пропущен.")
        if updated:
            print_info("Конфигурация обновлена.")

    def init(self, repo_root):
        if not self.config_file.exists():
            zones = self.discover_and_save_zones(repo_root)
            print_info(f"Создан файл zones_config.json с {len(zones)} кластерами.")
        else:
            zones = self.load_zones()
            self.check_and_update_zones(repo_root, zones)
        return zones

    def verify(self, repo_root, zones):
        """Самопроверка при старте: каждая зона существует, читаема и содержит конфиги."""
        print_info("Самопроверка доступности зон:")
        missing = []
        unreadable = []
        empty = []
        for zone in zones:
            base = repo_root / zone["path"]
            if not base.exists():
                missing.append(zone)
                print(f"  {_tag('ERROR', RED)} {zone['label']}: папка {zone['path']} не найдена")
                continue
            try:
                stand_prefixes = tuple(zone.get("stand_prefixes", ("cowork-",)))
                configs = [p for p in base.rglob(CONFIG) if p.parent.name.startswith(stand_prefixes)]
            except OSError as error:
                unreadable.append(zone)
                print(f"  {_tag('ERROR', RED)} {zone['label']}: папка {zone['path']} не читается ({error})")
                continue
            if not configs:
                empty.append(zone)
                print(f"  {_tag('WARN', YELLOW)} {zone['label']}: папка {zone['path']} не содержит config.yaml")
            else:
                print(f"  {_tag('OK', GREEN)} {zone['label']}: {len(configs)} конфиг(ов) в {zone['path']}")
        if missing or unreadable:
            print_error("Ошибка: некоторые зоны недоступны. Продолжение невозможно.")
            sys.exit(1)
        if empty:
            print(f"  {_tag('WARN', YELLOW)} В некоторых зонах нет конфигов — поиск по ним вернёт пусто.")
        print_info(f"Самопроверка завершена: зон проверено {len(zones)}, ошибок {len(missing) + len(unreadable)}.")


ZONES = ZoneManager(ZONES_CONFIG_FILE)

# --- Репозиторий argocd ---
def get_repo_root(start):
    if REPO_PATH.exists() and (REPO_PATH / ".git").exists():
        return REPO_PATH.resolve()
    for p in (start, start.parent, start.parent / "argocd"):
        if (p / ".git").exists():
            return p.resolve()
    cur = start.resolve()
    while cur != cur.parent:
        if (cur / ".git").exists():
            return cur
        cur = cur.parent
    raise FileNotFoundError("Не найден .git")


def _auto_restore_repo(repo_root):
    """Восстанавливает удалённые вручную файлы из последнего коммита; изменённые не трогает."""
    st = _git(repo_root, "status", "--porcelain")
    lines = [l for l in st.stdout.splitlines() if l.strip()]
    if not lines:
        return
    # Удалённые файлы: XY где Y='D' (working tree) или X='D' (staged delete)
    deleted_paths = []
    for line in lines:
        xy = line[:2]
        path = line[3:]
        if 'D' in xy:
            deleted_paths.append(path)
    modified = [l for l in lines if 'D' not in l[:2] and not l.startswith("??")]
    if deleted_paths:
        print_warn(f"Обнаружено {len(deleted_paths)} удалённых файла(ов) "
                   f"(возможно, содержимое переносили вручную).")
        answer = input_with_timeout("Восстановить их из последнего коммита? (yes/no): ", INPUT_TIMEOUT)
        if not (answer and answer.lower() in ("y", "yes")):
            print_info("Восстановление пропущено — поиск продолжится по текущему состоянию репозитория.")
            return
        # git restore только удалённые файлы, не трогая изменённые
        res = _git(repo_root, "restore", "--", *deleted_paths)
        if res.returncode != 0:
            # fallback: если restore по списку не сработал (много файлов) — через checkout
            res2 = _git(repo_root, "checkout", "--", ".")
            if res2.returncode != 0:
                print_error(f"Не удалось восстановить файлы: {res.stderr.strip()}")
                print_info("Выполните вручную: git restore . && git status")
                sys.exit(1)
        print_info("Удалённые файлы восстановлены из последнего коммита.")
    elif modified:
        print_warn(f"Есть локальные изменения ({len(modified)} файл(ов)) — они сохранены как есть, "
                   f"поиск использует актуальное содержимое.")

def _save_repo_path(target, quiet=False):
    REPO_LOCAL_FILE.write_text(str(target.resolve()), encoding="utf-8")
    if not quiet:
        print_info(f"Путь сохранён в {REPO_LOCAL_FILE.name} — в следующий раз спрашивать не буду.")

def _clone_repo(target):
    target.parent.mkdir(parents=True, exist_ok=True)
    res = subprocess.run(["git", "clone", REPO_URL, str(target)], capture_output=True, text=True)
    if res.returncode != 0:
        print_error(f"git clone failed: {res.stderr.strip()}")
        return False
    return True

def _repo_has_content(repo_root):
    """В корне репозитория есть хотя бы одна известная корневая папка зон."""
    return any((repo_root / d).exists() for d in ALLOWED_REPO_DIRS)

def _prompt_repo_root(script_dir):
    """Путь к репозиторию: сохранённый → ввод → клонирование. Результат запоминается."""
    # Попробуем ранее сохранённый путь
    if REPO_LOCAL_FILE.exists():
        saved = REPO_LOCAL_FILE.read_text(encoding="utf-8").strip()
        if saved and (Path(saved) / ".git").exists():
            print_info(f"Использую сохранённый путь к репозиторию: {saved}")
            return Path(saved).resolve()
        print_warn(f"Сохранённый путь недоступен ({saved}), укажу заново.")

    while True:
        raw = input_with_timeout(
            f"{action_label()} Укажите путь к локальному репозиторию argocd "
            f"(Enter — склонировать в {REPO_PATH}): ", INPUT_TIMEOUT
        )
        if raw is None:
            print_error("Таймаут ожидания. Запустите finder повторно.")
            sys.exit(1)
        target = Path(raw.strip()).expanduser() if raw.strip() else REPO_PATH
        if (target / ".git").exists():
            _save_repo_path(target)
            return target.resolve()
        answer = input_with_timeout(
            f"[WARN] В {target} нет .git. Склонировать туда {REPO_URL}? (y/n): ", INPUT_TIMEOUT
        )
        if answer is None:
            print_error("Таймаут ожидания. Запустите finder повторно.")
            sys.exit(1)
        if answer.strip().lower() in ("y", "yes"):
            try:
                if _clone_repo(target):
                    _save_repo_path(target, quiet=True)
                    print_info(f"Репозиторий склонирован в {target}, путь сохранён.")
                    return target.resolve()
                continue
            except FileNotFoundError:
                print_error("git не найден в системе. Установите git и запустите finder повторно.")
                sys.exit(1)
        else:
            print_warn("Введите существующий путь к репозиторию (с папкой .git).")

def _git(repo_root, *args):
    return subprocess.run(["git", *args], cwd=repo_root, capture_output=True, text=True)


def _stop_spinner(stop_event, spinner):
    stop_event.set()
    spinner.join()
    if sys.stdout.isatty():
        print()


def _extract_subdomain(config_path):
    """subdomain из config.yaml (для отчёта об изменениях)."""
    return read_config(config_path).get("cowork_subdomain") or "?"


def _report_changes(repo_root, news_dir, head_before, head_after):
    diff_stat = _git(repo_root, "diff", "--stat", head_before, head_after).stdout
    diff_full = _git(repo_root, "diff", head_before, head_after).stdout
    saved_path = None
    if diff_full:
        news_dir.mkdir(parents=True, exist_ok=True)
        now = datetime.now()
        fname = f"changes_{now.strftime('%Y%m%d_%H%M%S')}.txt"
        saved_path = news_dir / fname
        (saved_path).write_text(f"Изменения от {now}\n{'=' * 40}\n{diff_full}", encoding="utf-8")
    if diff_stat:
        files = _git(repo_root, "diff", "--name-only", head_before, head_after).stdout.strip().split("\n")
        print_info("Изменённые файлы (cowork_subdomain):")
        for f in files:
            if f:
                print(f"  {f} ({colorize(_extract_subdomain(repo_root / f), YELLOW)})")
    else:
        print_warn("Изменения затянуты, но статистика пуста.")
    if saved_path:
        print_info(f"Полные изменения сохранены в {saved_path}")


def pull_repo(repo_root, fatal=True):
    """git fetch + pull; возвращает (remote_url, head_before, head_after).

    fatal=False — ошибка не завершает программу, а возвращает None
    (используется фоновым обновлением из check_and_notify)."""
    stop_event = threading.Event()
    spinner = threading.Thread(target=animate_spinner, args=(stop_event,), daemon=True)
    spinner.start()
    suffix = " " if sys.stdout.isatty() else "..."
    print(f"{_tag('INFO', GREEN)} Обновление репозитория{suffix}", end='', flush=True)
    try:
        remote_url = _git(repo_root, "remote", "get-url", "origin").stdout.strip()
        head_before = _git(repo_root, "rev-parse", "HEAD").stdout.strip()
        fetch = _git(repo_root, "fetch", "origin")
        if fetch.returncode != 0:
            raise RuntimeError(f"git fetch failed: {fetch.stderr}")
        branch = _git(repo_root, "symbolic-ref", "--short", "HEAD").stdout.strip()
        if not branch:
            raise RuntimeError("Не удалось определить текущую ветку.")
        pull = _git(repo_root, "pull", "origin", branch)
        if pull.returncode != 0:
            raise RuntimeError(f"git pull failed: {pull.stderr}")
        head_after = _git(repo_root, "rev-parse", "HEAD").stdout.strip()
    except Exception as e:
        print_error(str(e))
        if not fatal:
            return None
        sys.exit(1)
    finally:
        _stop_spinner(stop_event, spinner)
    if remote_url:
        print_info(f"Удалённый репозиторий: {remote_url}")
    return remote_url, head_before, head_after


def report_pull(repo_root, news_dir, remote_url, head_before, head_after):
    """Отчёт о результате pull — вызывается после самопроверки зон."""
    if head_before and head_after and head_before != head_after:
        _report_changes(repo_root, news_dir, head_before, head_after)
    else:
        print_info("Новых изменений нет.")


# --- Фоновая проверка изменений в репозитории ---
class RepoChangeWatcher:
    """Фоновый наблюдатель за репозиторием argocd.

    Раз в REPO_CHECK_INTERVAL секунд выполняет тихий `git fetch origin` и
    сравнивает HEAD с origin/<ветка>. Если появились новые коммиты —
    выставляется флаг; уведомление показывает check_and_notify(), который
    вызывается ТОЛЬКО в момент простоя (перед экраном критериев поиска),
    чтобы не прерывать работу в меню. Пока накоплено больше
    REPO_FAST_CHECK_THRESHOLD коммитов и пользователь ещё не ответил,
    проверки идут чаще — каждые REPO_FAST_CHECK_INTERVAL секунд, чтобы
    счётчик изменений в уведомлении обновлялся без ожидания полного
    интервала. Ошибки сети/гита игнорируются — повторная попытка через
    следующий интервал."""

    def __init__(self, repo_root, interval=REPO_CHECK_INTERVAL):
        self.repo_root = Path(repo_root)
        self.interval = interval
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._changes_seen = False
        self._declined_remote = None  # состояние, за которое уже спрашивали и получили «no»
        self._remote_head = None      # текущее состояние origin/<ветка> после последнего fetch
        self._change_count = 0        # сколько коммитов на origin/ветке сверх локального HEAD
        self._thread = None

    def start(self):
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()

    def _run(self):
        while not self._stop_event.wait(self._current_interval()):
            try:
                self._check_once()
            except Exception:
                pass  # фоновая проверка: ошибки молча глотаем, повторим позже

    def _current_interval(self):
        """Интервал до следующей проверки.

        Если накопилось больше REPO_FAST_CHECK_THRESHOLD коммитов и мы ещё
        не спрашивали об этом состоянии — проверяем чаще (каждые
        REPO_FAST_CHECK_INTERVAL секунд), чтобы счётчик в уведомлении
        обновлялся без ожидания полного интервала."""
        with self._lock:
            if (self._changes_seen and self._declined_remote is None
                    and self._change_count > REPO_FAST_CHECK_THRESHOLD):
                return REPO_FAST_CHECK_INTERVAL
        return self.interval

    def _check_once(self):
        branch_res = _git(self.repo_root, "symbolic-ref", "--short", "HEAD")
        if branch_res.returncode != 0:
            return
        branch = branch_res.stdout.strip()
        if _git(self.repo_root, "fetch", "origin").returncode != 0:
            return
        local = _git(self.repo_root, "rev-parse", "HEAD")
        remote = _git(self.repo_root, "rev-parse", f"origin/{branch}")
        if local.returncode != 0 or remote.returncode != 0:
            return
        # Сколько коммитов на origin/ветке сверх локального HEAD (для уведомления).
        count_res = _git(self.repo_root, "rev-list", "--count", f"HEAD..origin/{branch}")
        try:
            count = int(count_res.stdout.strip()) if count_res.returncode == 0 else 0
        except ValueError:
            count = 0
        with self._lock:
            if remote.stdout.strip() != local.stdout.strip():
                # Новое состояние на удалёнке (или первое замеченное): спрашиваем.
                # Если это то же самое, за которое уже ответили «no» — не спамим.
                if remote.stdout.strip() != self._declined_remote:
                    self._changes_seen = True
                self._remote_head = remote.stdout.strip()
                self._change_count = count

    def pending_remote(self):
        """Состояние origin/<ветка>, за которое ещё не спрашивали (или None)."""
        with self._lock:
            if self._changes_seen and self._declined_remote is None:
                return self._remote_head
            return None

    def mark_declined(self, remote_head):
        """Пользователь ответил «no» — не спрашиваем снова об этом же состоянии."""
        with self._lock:
            self._declined_remote = remote_head
            if remote_head == self._remote_head:
                self._changes_seen = False

    def has_changes(self):
        with self._lock:
            return self._changes_seen

    def change_count(self):
        """Сколько новых коммитов на origin/ветке (0, если не удалось посчитать)."""
        with self._lock:
            return self._change_count

    def clear_changes(self):
        with self._lock:
            self._changes_seen = False
            self._declined_remote = None


WATCHER = None  # создаётся в main() после стартового pull


def check_and_notify(repo_root, news_dir, zones):
    """Уведомление о новых изменениях в репозитории.

    Вызывать только в простое — перед экраном критериев поиска. При
    подтверждении выполняет pull, пересобирает зоны (in-place, чтобы
    ссылки в main остались валидными) и печатает отчёт об изменениях."""
    if WATCHER is None or not WATCHER.has_changes():
        return False
    # Фиксируем состояние удалёнки ДО вопроса: ответ «no» относится именно к нему.
    remote_head = WATCHER.pending_remote()
    count = WATCHER.change_count()
    if count > 0:
        # Скобки — белые, только цифра жёлтая (вне TTY без ANSI-кодов).
        if sys.stdout.isatty():
            counter = f"{WHITE}({YELLOW}{count}{RESET}){RESET}"
        else:
            counter = f"({count})"
        print_warn(f"Найдены изменения в конфигурациях стендов {counter}.")
    else:
        print_warn("Найдены изменения в конфигурациях стендов.")
    answer = input_with_timeout("Обновить локальный репозиторий? (yes/no): ", INPUT_TIMEOUT)
    if not (answer and answer.lower() in ("y", "yes")):
        # «no» — не дёргаем за то же состояние удалёнки снова, пока не появится новый коммит.
        WATCHER.mark_declined(remote_head)
        return False
    pull_result = pull_repo(repo_root, fatal=False)
    if pull_result is None:
        WATCHER.mark_declined(remote_head)
        return False
    zones[:] = ZONES.init(repo_root)
    ZONES.verify(repo_root, zones)
    report_pull(repo_root, news_dir, *pull_result)
    WATCHER.clear_changes()
    return True

# --- Подсчёт утилизации токенов (xlsx → ClickHouse → xlsx) ---
def _import_openpyxl():
    """openpyxl для чтения/записи xlsx; если его нет в Python — ставим на лету (pip)."""
    try:
        import openpyxl
        return openpyxl
    except ImportError as error:
        # Диагностика: показываем, КАКИМ python запущен скрипт и что именно не нашлось.
        print_warn(f"openpyxl не найден ({error}).")
        print_info(f"Интерпретатор: {sys.executable}")
        print_info(f"Версия: {sys.version.split()[0]}")
        print_info(f"Пути поиска: {sys.path}")
    print_warn("Пробую установить через pip в этот же интерпретатор...")
    try:
        subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "openpyxl"], check=True)
        import openpyxl
        print_info(f"openpyxl установлен: {openpyxl.__version__}")
        return openpyxl
    except Exception as error:
        print_error(f"Не удалось установить openpyxl автоматически: {error}")
        print_info("Установите вручную: python -m pip install openpyxl")
        return None
TOKENS_DIR_NAME = "tokens"
TOKENS_SOURCE_FILE = "tokens.xlsx"
def _build_subdomain_to_stand(repo_root, zones):
    """Маппинг cowork_subdomain → stand_name по всем конфигам зон репозитория."""
    mapping = {}
    conflicts = 0
    for zone in zones:
        base = repo_root / zone["path"]
        if not base.exists():
            continue
        prefixes = tuple(zone.get("stand_prefixes", ("cowork-",)))
        for config_path in base.rglob(CONFIG):
            if not config_path.parent.name.startswith(prefixes):
                continue
            data = read_config(config_path)
            sub = data.get("cowork_subdomain")
            stand = data.get("stand_name")
            if not sub or not stand:
                continue
            key = sub.strip().lower()
            if key in mapping and mapping[key] != stand:
                conflicts += 1
            else:
                mapping[key] = stand
    return mapping, conflicts


def _build_inn_to_stand(repo_root, zones):
    """Маппинг ИНН → stand_name по всем конфигам зон репозитория (fallback)."""
    mapping = {}
    for zone in zones:
        base = repo_root / zone["path"]
        if not base.exists():
            continue
        prefixes = tuple(zone.get("stand_prefixes", ("cowork-",)))
        for config_path in base.rglob(CONFIG):
            if not config_path.parent.name.startswith(prefixes):
                continue
            data = read_config(config_path)
            inn = data.get("inn")
            stand = data.get("stand_name")
            if not inn or not stand:
                continue
            key = inn.strip()
            if key not in mapping:
                mapping[key] = stand
    return mapping
def _token_utilization_sql(stands, days):
    """Один запрос на все стенды: GROUP BY environment, чтобы результат раскидать по строкам."""
    quoted = ", ".join(f"'{s}'" for s in stands)
    return (
        "SELECT environment, count() AS calls, "
        "sum(usage_details['input']) AS input_tokens, sum(usage_details['output']) AS output_tokens, "
        "sum(usage_details['total']) AS total_tokens "
        "FROM default.observations "
        f"WHERE environment IN ({quoted}) AND type = 'GENERATION' "
        f"AND start_time >= now() - INTERVAL {days} DAY "
        "GROUP BY environment FORMAT TabSeparated"
    )
def _parse_ch_env_output(raw_lines):
    """Разбор вывода clickhouse-client с колонкой environment.
    Возвращает dict: environment → (calls, inp, outp, tot)."""
    def _num(s):
        s = s.strip()
        if s in ("", r"\N", "NULL"):
            return 0
        return int(float(s))
    result = {}
    for line in raw_lines:
        parts = line.split("\t")
        if len(parts) >= 5:
            env_name, nums = parts[0], parts[1:5]
        else:
            toks = line.split()
            if len(toks) < 5:
                continue
            env_name, nums = toks[0], toks[-4:]
        try:
            calls, inp, outp, tot = (_num(n) for n in nums)
        except ValueError:
            continue
        if tot == 0:
            tot = inp + outp
        result[env_name.strip()] = (calls, inp, outp, tot)
    return result
TOKENS_RESULT_FILE = "результат clickhouse.txt"
TOKENS_USERS_RESULT_FILE = "результат пользователи.txt"

_LIST_USERS_HEREDOC = """/app/services/auth/.venv/bin/python <<'PY'
import asyncio, sys, httpx
from datetime import datetime, timezone
try:
    from auth_service.bootstrap.config import get_settings
except ModuleNotFoundError:
    from auth_service.config import get_settings

async def main():
    settings = get_settings()
    base_url = settings.provider_issuer_url.split("/realms/")[0]
    realm = settings.provider_realm

    async with httpx.AsyncClient() as http:
        token_resp = await http.post(
            f"{base_url}/realms/{realm}/protocol/openid-connect/token",
            data={
                "grant_type": "client_credentials",
                "client_id": settings.provider_client_id,
                "client_secret": settings.provider_client_secret,
            },
        )
        token_resp.raise_for_status()
        access_token = token_resp.json()["access_token"]

        all_users = []
        first = 0
        page_size = 100
        while True:
            resp = await http.get(
                f"{base_url}/admin/realms/{realm}/users",
                params={"first": first, "max": page_size},
                headers={"Authorization": f"Bearer {access_token}"},
            )
            resp.raise_for_status()
            users = resp.json()
            if not users:
                break
            all_users.extend(users)
            if len(users) < page_size:
                break
            first += page_size

    all_users.sort(key=lambda u: u.get("email", "").lower())
    print(f"Total: {len(all_users)}")
    print()
    for u in all_users:
        email = u.get("email", "—")
        name = f"{u.get('firstName', '')} {u.get('lastName', '')}".strip() or "—"
        uid = u.get("id", "—")
        status = "active" if u.get("enabled") else "disabled"
        ts = u.get("createdTimestamp")
        created = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d") if ts else "—"
        print(f"{email}  |  {name}  |  {status}  |  created={created}  |  id={uid}")

asyncio.run(main())
PY"""


def _build_stand_to_zone(repo_root, zones):
    """Маппинг stand_name → zone_dict (для открытия правильного ArgoCD-пода).
    Возвращает dict с ключами code, app_template, argocd_base."""
    mapping = {}
    # Lookup: label (из zones_config) → code (из WORKSPACE_ZONES).
    code_by_label = {z["cluster"]: z["code"] for z in WORKSPACE_ZONES}
    for zone in zones:
        base = repo_root / zone["path"]
        if not base.exists():
            continue
        prefixes = tuple(zone.get("stand_prefixes", ("cowork-",)))
        code = code_by_label.get(zone.get("label", ""), "")
        zone_for_exec = {
            "code": code,
            "app_template": zone.get("app_template"),
            "argocd_base": zone.get("argocd_base"),
        }
        for config_path in base.rglob(CONFIG):
            if not config_path.parent.name.startswith(prefixes):
                continue
            data = read_config(config_path)
            stand = data.get("stand_name")
            if stand and stand not in mapping:
                mapping[stand] = zone_for_exec
    return mapping


def _parse_users_output(raw_text):
    """Из вывода скрипта подсчёта пользователей извлекает число пользователей.
    Ищет строку вида 'Total: N'. ANSI-коды отбрасываются перед поиском.
    Возвращает int или None."""
    import re
    # Убираем ANSI-escape последовательности (веб-терминал ArgoCD добавляет подсветку)
    clean = re.sub(r"\x1b\[[0-9;]*m", "", raw_text)
    m = re.search(r"Total:\s*(\d+)", clean)
    if m:
        return int(m.group(1))
    return None


def _count_users_for_stands(stands, stand_to_zone, tokens_dir):
    """По очереди открывает heimdall-под для каждого стенда, принимает результат.
    Скрипт сохраняется в exec/ один раз перед циклом.
    Возвращает dict: stand_name → int (число пользователей)."""
    users_by_env = {}
    result_path = tokens_dir / TOKENS_USERS_RESULT_FILE
    # Сохраняем скрипт в exec/ один раз — он одинаковый для всех стендов.
    EXEC_DIR.mkdir(parents=True, exist_ok=True)
    script_path = EXEC_DIR / "list_users.sh"
    script_path.write_text(_LIST_USERS_HEREDOC, encoding="utf-8")
    print_info(f"Скрипт сохранён: {script_path}")
    for idx, stand in enumerate(stands, 1):
        zone = stand_to_zone.get(stand)
        if zone is None:
            print_warn(f"Зона не найдена для стенда {stand} — пропускаю.")
            continue
        print_divider()
        print_info(f"Стенд {idx}/{len(stands)}: {stand}")
        open_pod_exec(zone["code"], stand, "heimdall", zone.get("app_template"), zone.get("argocd_base"))
        print_info(f"Скопируйте содержимое {script_path.name} целиком в веб-терминал пода и запустите.")
        result_path.write_text("", encoding="utf-8")
        print_info(f"Скопируйте вывод команды в файл: {result_path}")
        answer = input(f"{action_label()} Заполнили файл? (yes/no/skip, Enter — yes): ").strip().lower()
        if answer == "skip":
            print_warn(f"Пропущен стенд {stand}.")
            continue
        if answer in ("n", "no"):
            print_warn("Операция отменена.")
            break
        try:
            raw = result_path.read_text(encoding="utf-8")
        except OSError as error:
            print_error(f"Не удалось прочитать файл: {error}")
            continue
        count = _parse_users_output(raw)
        if count is None:
            print_warn(f"Не удалось разобрать результат для {stand}: строка «Total: N» не найдена.")
            answer2 = input_with_timeout(f"{action_label()} Повторить? (yes/no/skip): ", INPUT_TIMEOUT)
            if answer2 and answer2.strip().lower() in ("y", "yes"):
                continue
            print_warn(f"Пропущен стенд {stand}.")
            continue
        users_by_env[stand] = count
        print_info(f"Пользователей на {stand}: {_fmt_int(count)}")
    return users_by_env
def _read_result_file(result_path):
    """Строки результата из промежуточного файла; пропуская служебные строки
    веб-терминала (root@..., #, $, эхо команды) и пустые."""
    try:
        raw = result_path.read_text(encoding="utf-8")
    except OSError as error:
        print_error(f"Не удалось прочитать файл: {error}")
        return []
    lines = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(("root@", "#", "$")) or "clickhouse-client" in stripped:
            continue
        lines.append(line.rstrip())
    return lines
def _write_token_report_xlsx(src_path, out_path, row_stands, totals_by_env, days, users_by_env=None):
    """Копирует исходную таблицу и заполняет столбец D аналитикой по токенам.
    row_stands — список stand_name (или None), выровненный по видимым непустым строкам.
    Обрабатывает ТОЛЬКО видимые (развернутые) строки; свернутые (hidden) — не трогает."""
    openpyxl = _import_openpyxl()
    if openpyxl is None:
        raise RuntimeError("openpyxl недоступен")
    wb = openpyxl.load_workbook(str(src_path))
    ws = wb[wb.sheetnames[0]]
    filled = no_data = missing = skipped_hidden = 0
    idx = 0
    for row in range(2, ws.max_row + 1):
        # Свернутая строка — пропускаем целиком, ячейку D не трогаем.
        if ws.row_dimensions[row].hidden:
            skipped_hidden += 1
            continue
        cell_a = ws.cell(row=row, column=1).value
        cell_b = ws.cell(row=row, column=2).value
        a_empty = cell_a is None or str(cell_a).strip() == ""
        b_empty = cell_b is None or str(cell_b).strip() == ""
        if a_empty and b_empty:
            continue
        stand = row_stands[idx] if idx < len(row_stands) else None
        idx += 1
        if stand is None:
            text = "Не найден в конфигурациях репозитория"
            missing += 1
        elif stand not in totals_by_env:
            text = f"Данных нет: за последние {days} дн. вызовов LLM не найдено"
            no_data += 1
        else:
            calls, inp, outp, tot = totals_by_env[stand]
            parts = [f"Вызовов LLM: {_fmt_int(calls)}"]
            if users_by_env and stand in users_by_env:
                parts.append(f"Пользователей: {_fmt_int(users_by_env[stand])}")
            parts.append(f"Потрачено за период: {_fmt_int(tot)} токенов")
            parts.append(f"  запрос к модели (input):   {_fmt_int(inp)}")
            parts.append(f"  ответ модели (output):     {_fmt_int(outp)}")
            text = "\n".join(parts)
            filled += 1
        cell = ws.cell(row=row, column=4, value=text)
        cell.alignment = openpyxl.styles.Alignment(wrap_text=True, vertical="top")
    wb.save(str(out_path))
    return filled, no_data, missing, skipped_hidden
def token_utilization_report(repo_root, script_dir, zones):
    """Автоматизация подсчёта токенов: xlsx со списком subdomain → один запрос
    clickhouse-client на все стенды → результат вставляется из терминала →
    новая таблица в папке «Подсчет токенов» со столбцом K, заполненным аналитикой."""
    print_divider()
    print_action("Подсчёт утилизации токенов")
    tokens_dir = script_dir / TOKENS_DIR_NAME
    if not tokens_dir.exists():
        tokens_dir.mkdir(parents=True)
        print_info(f"Создана папка: {tokens_dir}")
    else:
        print_info(f"Папка готова: {tokens_dir}")
    # Исходная таблица: ищем в папке подсчёта, затем рядом со скриптом.
    src = tokens_dir / TOKENS_SOURCE_FILE
    if not src.exists():
        alt = script_dir / TOKENS_SOURCE_FILE
        if alt.exists():
            src = alt
        else:
            print_warn(f"Файл «{TOKENS_SOURCE_FILE}» не найден в {tokens_dir} и рядом со скриптом.")
            answer = input_with_timeout(f"{action_label()} Укажите полный путь к файлу (Enter — отмена): ", INPUT_TIMEOUT)
            if answer is None or not answer.strip():
                print_warn("Операция отменена.")
                return
            src = Path(answer.strip())
            if not src.exists():
                print_error(f"Файл не найден: {src}")
                return
    openpyxl = _import_openpyxl()
    if openpyxl is None:
        return
    try:
        # Без read_only: нужен доступ к row_dimensions (hidden) — берём ТОЛЬКО
        # развернутые (видимые) строки, свернутые в запрос не включаем.
        wb = openpyxl.load_workbook(str(src))
        ws = wb[wb.sheetnames[0]]
        subs = []
        inns = []
        for row in range(2, ws.max_row + 1):
            if ws.row_dimensions[row].hidden:
                continue
            v_a = ws.cell(row=row, column=1).value
            v_b = ws.cell(row=row, column=2).value
            a = str(v_a).strip() if v_a is not None else ""
            b = str(v_b).strip() if v_b is not None else ""
            # Строка участвует, если заполнен хотя бы один критерий: subdomain (A) или ИНН (B).
            if not a and not b:
                continue
            subs.append(a)
            inns.append(b)
        wb.close()
    except Exception as error:
        print_error(f"Не удалось прочитать файл: {error}")
        return
    if not subs:
        print_error("В таблице нет ни одного subdomain (столбец A) или ИНН (столбец B).")
        return
    print_info(f"Видимых (развернутых) строк в таблице: {len(subs)}, уникальных subdomain: {len(set(s.lower() for s in subs))}.")
    # Период — как в пункте 8.
    print("  Период:")
    print("   1. Последние 7 дней")
    print("   2. Последние 30 дней")
    print("   3. Другой период (дней)")
    choice = input_with_timeout("Выберите период (1-3): ", INPUT_TIMEOUT)
    if choice is None:
        print_warn("Таймаут ожидания. Операция отменена.")
        return
    choice = choice.strip()
    if choice == "1":
        days = 7
    elif choice == "2":
        days = 30
    elif choice.isdigit() and 1 <= int(choice) <= 365:
        days = int(choice)
    else:
        print_warn("Неверный период, операция отменена.")
        return
    # Маппинг subdomain → stand_name и ИНН → stand_name по конфигам репозитория.
    print_info("Собираю маппинг из конфигураций репозитория...")
    mapping, conflicts = _build_subdomain_to_stand(repo_root, zones)
    inn_mapping = _build_inn_to_stand(repo_root, zones)
    if conflicts:
        print_warn(f"Обратите внимание: {conflicts} конфликт(а) — один subdomain встречается в разных стендах; взят первый.")
    # Разрешаем каждую строку: сначала по subdomain (A), затем по ИНН (B).
    row_stands = []
    for sub, inn in zip(subs, inns):
        stand = mapping.get(sub.lower())
        if stand is None and inn:
            stand = inn_mapping.get(inn)
        row_stands.append(stand)
    stands = list(dict.fromkeys(s for s in row_stands if s))
    resolved = sum(1 for s in row_stands if s)
    print_info(f"Соответствие найдено для {resolved}/{len(subs)} строк ({len(stands)} стендов).")
    unresolved = len(subs) - resolved
    if unresolved:
        print_warn(f"Не найдено в конфигурациях: {unresolved} строк — для них будет пометка.")
    if not stands:
        print_error("Ни одна строка из таблицы не найдена в конфигурациях репозитория (по subdomain и ИНН).")
        return
    sql = _token_utilization_sql(stands, days)
    ch_app_url = ("https://argocd-tb01.cowork.ru/applications/langfuse/langfuse"
                  "?view=tree&node=%2FPod%2Flangfuse%2Fchi-langfuse-default-0-0-0&tab=exec&resource=")
    open_url(ch_app_url)
    print_info("Открыт под chi-langfuse-default-0-0-0 (приложение langfuse, вкладка exec).")
    result_path = tokens_dir / TOKENS_RESULT_FILE
    result_path.write_text("", encoding="utf-8")
    print("Выполните команду в открытом веб-терминале пода:")
    print()
    print(colorize(f"clickhouse-client --query \"{sql}\"", CYAN))
    print()
    print_info(f"Скопируйте весь вывод команды в файл: {result_path}")
    answer = input(f"{action_label()} Заполнили файл? (yes/no, Enter — yes): ").strip().lower()
    if answer in ("n", "no"):
        print_warn("Операция отменена.")
        return
    lines = _read_result_file(result_path)
    if not lines:
        print_warn(f"Файл {result_path} пуст или содержит только служебные строки.")
        answer2 = input_with_timeout(f"{action_label()} Продолжить с пометкой «Данных нет»? (yes/no): ", INPUT_TIMEOUT)
        if answer2 is None or answer2.strip().lower() not in ("y", "yes"):
            print_warn("Операция отменена.")
            return
    totals_by_env = _parse_ch_env_output(lines)
    if not totals_by_env:
        print_warn("Не удалось разобрать результат: ожидается табличка «environment вызовы input output total» (табы или пробелы).")
        print_info("Если данных действительно нет — так и оставьте: в столбце K будет пометка «Данных нет».")
    else:
        matched = sum(1 for s in stands if s in totals_by_env)
        print_info(f"Разобрано результатов по {len(totals_by_env)} стендам, из запрошенных {len(stands)} найдено {matched}.")
    # Опциональный шаг: подсчёт пользователей на каждом стенде через heimdall-auth.
    # Считаем только для стендов, где были вызовы LLM (есть данные в ClickHouse).
    users_by_env = {}
    active_stands = [s for s in stands if s in totals_by_env]
    if active_stands:
        print_info(f"Подсчёт пользователей по {len(active_stands)} стендам с данными...")
        stand_to_zone = _build_stand_to_zone(repo_root, zones)
        users_by_env = _count_users_for_stands(active_stands, stand_to_zone, tokens_dir)
        if users_by_env:
            total_users = sum(users_by_env.values())
            print_info(f"Итого пользователей по всем стендам: {_fmt_int(total_users)}.")
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    out_path = tokens_dir / f"Подсчет утилизации_{stamp}.xlsx"
    try:
        filled, no_data, missing, skipped_hidden = _write_token_report_xlsx(src, out_path, row_stands, totals_by_env, days, users_by_env)
    except Exception as error:
        print_error(f"Не удалось сохранить Excel: {error}")
        return
    print_divider()
    print_info(f"Готово: {filled} строк с аналитикой, {no_data} без данных за период, {missing} не найдено в репозитории.")
    if skipped_hidden:
        print_info(f"Свернутые строки не тронуты: {skipped_hidden}.")
    print_info(f"Файл сохранён: {out_path}")

# --- Поиск стендов ---
_CONFIG_KEYS = ("stand_name", "cowork_subdomain", "admin_email", "expiry_date", "cowork_revision", "public_hostname", "status")


def read_config(file_path):
    try:
        content = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}
    data = {}
    for key in _CONFIG_KEYS:
        m = re.search(rf'{key}:\s*"?([^"\n]+)"?', content)
        data[key] = m.group(1).strip() if m else None
    m = re.search(r'(?:meta\.)?inn:\s*"?([^"\n]+)"?', content)
    data["inn"] = m.group(1).strip() if m else None
    data["broken"] = "# broken stand" in content
    return data


def _matches(data, criteria):
    for criterion, value in (
        (criteria.get("subdomain"), data.get("cowork_subdomain")),
        (criteria.get("email"), data.get("admin_email")),
        (criteria.get("stand_name"), data.get("stand_name")),
    ):
        if criterion and value and criterion.lower() in value.lower():
            return True
    inn = criteria.get("inn")
    return bool(inn and data.get("inn") and inn in data["inn"])


def _is_empty_stand(data):
    """Пустой (незаполненный) стенд: нет ни admin_email, ни ИНН, ни expiry_date."""
    return not (data.get("admin_email") or data.get("inn") or data.get("expiry_date"))


def _exact_match(data, criteria, folder):
    """Точное совпадение критерия поиска с именем папки/стенда, subdomain, email или ИНН.

    Если пользователь ввёл значение целиком (а не подстроку) — стенд показываем
    даже при фильтрации пустых/дефолтных."""
    folder_l = folder.strip().lower()
    for key, value in (
        ("stand_name", data.get("stand_name")),
        ("subdomain", data.get("cowork_subdomain")),
    ):
        crit = (criteria.get(key) or "").strip().lower()
        val = (value or "").strip().lower()
        if crit and (crit == val or crit == folder_l):
            return True
    email_crit = (criteria.get("email") or "").strip().lower()
    email_val = (data.get("admin_email") or "").strip().lower()
    if email_crit and email_crit == email_val:
        return True
    inn_crit = (criteria.get("inn") or "").strip()
    if inn_crit and data.get("inn") and inn_crit in data["inn"]:
        return True
    return False


def find_stands(root, zones, criteria):
    results = []
    for zone in zones:
        base = root / zone["path"]
        if not base.exists():
            continue
        stand_prefixes = tuple(zone.get("stand_prefixes", ("cowork-",)))
        for config_path in base.rglob(CONFIG):
            stand_dir = config_path.parent
            if not stand_dir.name.startswith(stand_prefixes):
                continue
            data = read_config(config_path)
            # Точное совпадение по имени — показываем даже «пустой»/дефолтный стенд.
            exact = _exact_match(data, criteria, stand_dir.name)
            # Показываем, если нашлось совпадение по полям конфига ИЛИ точное
            # совпадение критерия с именем папки/стенда (конфиг может быть неполным).
            if not _matches(data, criteria) and not exact:
                continue
            # Пустые стенды (без admin_email/ИНН/expiry_date) в выдачу не попадаем.
            if not exact and _is_empty_stand(data):
                continue
            # Дефолтный subdomain (совпадает с именем папки) — стенд незанятый, тоже не показываем.
            sub = (data.get("cowork_subdomain") or "").strip()
            if not exact and sub and sub == stand_dir.name:
                continue
            data["folder"] = stand_dir.name
            data["stand_dir"] = zone["label"]
            data["zone_path"] = zone["path"]
            data["url_template"] = zone["url"]
            data["config_path"] = config_path
            results.append(data)
    return results


# Подсветка status стенда: trial — жёлтый, grace — голубой, expired — красный, active — зелёный; неизвестные — фиолетовые.
_STATUS_COLORS = {
    "trial": YELLOW,
    "grace": CYAN,
    "expired": RED,
    "active": GREEN,
}


def _status_color(status):
    return _STATUS_COLORS.get((status or "").strip().lower(), MAGENTA)


def _print_stand(idx, data):
    print(f"\n{idx}. Папка: {data.get('folder', 'N/A')}")
    print(f"   Расположение: {data.get('stand_dir', 'N/A')}")
    print(f"   stand_name: {data.get('stand_name', 'N/A')}")
    print(f"   cowork_subdomain: {data.get('cowork_subdomain', 'N/A')}")
    if data.get('public_hostname'):
        print(f"   public_hostname: {data.get('public_hostname')}")
    print(f"   admin_email: {data.get('admin_email', 'N/A')}")
    print(f"   ИНН: {data.get('inn', 'N/A')}")
    print(f"   expiry_date: {data.get('expiry_date', 'N/A')}")
    if data.get('status'):
        print(f"   status: {colorize(data['status'], _status_color(data['status']))}")
    print(f"   cowork_revision: {data.get('cowork_revision', 'N/A')}")
    if data.get("broken"):
        print(f"   {colorize('[broken stand]', RED)}")


def select_stand(results):
    if len(results) == 1:
        return results[0]
    print_warn("Найдено несколько стендов.")
    for i, data in enumerate(results, 1):
        sub = data.get('cowork_subdomain', 'N/A')
        print(f"  {i}. {data['folder']} ({colorize(sub, YELLOW)})")
    while True:
        print_action("Введите номер стенда (0 для отмены):", end="")
        choice = input().strip()
        if choice == '0':
            return None
        if choice.isdigit() and 1 <= int(choice) <= len(results):
            return results[int(choice) - 1]
        print_warn("Неверный ввод, попробуйте снова.")

# --- Быстрые действия (браузер) ---
def open_links(selected, script_dir, zone=None):
    helper = script_dir / "helper.html"
    if helper.exists():
        open_file(str(helper))
    else:
        print_warn("helper.html не найден.")
    if zone and zone.get("links_app"):
        # Особенность зоны: приложение Admin Helper — не {folder}, а links_app
        # (sdb: cowork/cowork-heimdall вместо cowork/cowork).
        base = zone.get("argocd_base") or _argocd_base(zone["code"])
        open_url(f"{base}/applications/{selected['folder']}/{zone['links_app']}?resource=")
    else:
        url = selected['url_template'].format(folder=selected['folder'])
        open_url(url)


def open_tenant(selected):
    hostname = selected.get('public_hostname')
    if hostname:
        open_url(f"https://{hostname}")
        return
    subdomain = selected.get('cowork_subdomain')
    if not subdomain:
        print_warn("Не указан cowork_subdomain, невозможно открыть тенант.")
        return
    open_url(f"https://{subdomain}.cowork.ru")


def open_tenant_console():
    open_url("https://tenant-console.cowork.ru/")


def open_faq_agent():
    open_url(FAQ_AGENT_URL)


def open_stand_config(selected):
    base_url = "https://git.sberdevices.ru/gigabb/infra/cowork/argocd/-/tree/master"
    zone_path = selected.get('zone_path')
    folder = selected.get('folder')
    if not zone_path or not folder:
        print_warn("Не удалось определить путь к конфигурации стенда.")
        return
    open_url(f"{base_url}/{zone_path}/{folder}?ref_type=heads")

# --- Управление пространствами: шаблоны сгенерированных скриптов ---
# Полные шаблоны (тело — рабочие версии change_workspace_owner / add_all_users_to_workspace), значения подставляются
# через .replace(), чтобы не экранировать фигурные скобки во внутренних f-строках.

_SCRIPT_COMMON_HELPERS = '''# ===== Общие хелперы встроенных скриптов (импорты + DSN + запросы) =====
import asyncio
import os
import sys

try:
    import asyncpg
except ImportError:
    # Под debug-ubuntu обновили — asyncpg из него вынесли; ставим на лету (pip в поде есть).
    import subprocess
    import sys as _sys
    try:
        subprocess.check_call([_sys.executable, "-m", "pip", "install", "--quiet", "asyncpg"])
    except subprocess.CalledProcessError:
        # Ubuntu 24.04 / PEP 668: без флага установка запрещена
        subprocess.check_call([_sys.executable, "-m", "pip", "install", "--quiet", "--break-system-packages", "asyncpg"])
    import asyncpg

WORKSPACE_NAMESPACE = "workspace"

def get_database_url(variable_names: tuple[str, ...], default: str) -> str:
    for name in variable_names:
        value = os.environ.get(name)
        if value:
            return value
    return default

async def resolve_user_id(connection: asyncpg.Connection, email: str) -> str:
    user_id = await connection.fetchval(
        "SELECT id::text FROM users WHERE lower(email) = lower($1)", email
    )
    if user_id is None:
        raise ValueError(f"Пользователь {email} не найден в users (yggdrasil)")
    return user_id

async def get_workspace_state(connection: asyncpg.Connection, workspace_id: str) -> dict:
    row = await connection.fetchrow(
        """
        SELECT id::text AS id, name, type, status, trash_state,
               owner_id::text AS owner_id
        FROM workspaces
        WHERE id = $1
        """,
        workspace_id,
    )
    if row is None:
        raise ValueError(f"Workspace not found: {workspace_id}")
    if row["status"] == "archived" or row["trash_state"] is not None:
        raise ValueError(f"Workspace is archived or trashed: {workspace_id}")
    return dict(row)

async def get_member_role(connection: asyncpg.Connection, workspace_id: str, user_id: str) -> str | None:
    return await connection.fetchval(
        """
        SELECT role_id
        FROM workspace_members
        WHERE workspace_id = $1 AND user_id = $2
        """,
        workspace_id, user_id,
    )

async def get_grant(
    heimdall_connection: asyncpg.Connection, user_id: str, workspace_id: str
) -> str | None:
    return await heimdall_connection.fetchval(
        """
        SELECT role_id
        FROM access_grants
        WHERE user_id = $1
          AND namespace = $2
          AND resource_id = $3
        """,
        user_id, WORKSPACE_NAMESPACE, workspace_id,
    )
'''
_CHANGE_OWNER_TEMPLATE = '''

OWNER_ROLE_ID = "workspace.owner"
MEMBER_ROLE_ID = "workspace.member"

WORKSPACE_IDS = [
__IDS__
]
NEW_OWNER_EMAIL = __EMAIL__
# True — только показать, что будет сделано, ничего не менять
DRY_RUN = __DRY__

DEFAULT_YGGDRASIL_URL = __YGG__
DEFAULT_HEIMDALL_URL = __HM__

async def user_exists(connection: asyncpg.Connection, user_id: str) -> bool:
    return await connection.fetchval(
        "SELECT EXISTS(SELECT 1 FROM users WHERE id = $1)", user_id
    )

async def get_members(connection: asyncpg.Connection, workspace_id: str) -> list[dict]:
    return [
        dict(r)
        for r in await connection.fetch(
            """
            SELECT user_id::text AS user_id, role_id
            FROM workspace_members
            WHERE workspace_id = $1
            ORDER BY user_id
            """,
            workspace_id,
        )
    ]

async def upsert_grant(
    heimdall_connection: asyncpg.Connection,
    user_id: str,
    workspace_id: str,
    role_id: str,
) -> None:
    await heimdall_connection.execute(
        """
        INSERT INTO access_grants (user_id, namespace, resource_id, role_id)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (user_id, namespace, resource_id)
        DO UPDATE SET role_id = EXCLUDED.role_id
        """,
        user_id, WORKSPACE_NAMESPACE, workspace_id, role_id,
    )

async def transfer(
    yggdrasil_connection: asyncpg.Connection,
    heimdall_connection: asyncpg.Connection,
    workspace_id: str,
    old_owner_id: str,
    new_owner_id: str,
    old_owner_alive: bool,
) -> None:
    # 1) Yggdrasil: workspaces.owner_id
    await yggdrasil_connection.execute(
        "UPDATE workspaces SET owner_id = $2 WHERE id = $1",
        workspace_id, new_owner_id,
    )

    # 2) Yggdrasil: новый владелец — owner в workspace_members
    #    (если записи ещё нет — создаём, если есть — обновляем роль)
    await yggdrasil_connection.execute(
        """
        INSERT INTO workspace_members (workspace_id, user_id, role_id)
        VALUES ($1, $2, $3)
        ON CONFLICT (workspace_id, user_id)
        DO UPDATE SET role_id = EXCLUDED.role_id
        """,
        workspace_id, new_owner_id, OWNER_ROLE_ID,
    )

    # 3) Старый владелец
    if old_owner_alive:
        # Пользователь жив — понижаем до member, он остаётся участником
        await yggdrasil_connection.execute(
            """
            UPDATE workspace_members
            SET role_id = $3
            WHERE workspace_id = $1 AND user_id = $2
            """,
            workspace_id, old_owner_id, MEMBER_ROLE_ID,
        )
        await upsert_grant(heimdall_connection, old_owner_id, workspace_id, MEMBER_ROLE_ID)
    else:
        # Пользователь удалён со стенда — чистим zombie-записи
        await yggdrasil_connection.execute(
            """
            DELETE FROM workspace_members
            WHERE workspace_id = $1 AND user_id = $2
            """,
            workspace_id, old_owner_id,
        )
        await heimdall_connection.execute(
            """
            DELETE FROM access_grants
            WHERE user_id = $1
              AND namespace = $2
              AND resource_id = $3
            """,
            old_owner_id, WORKSPACE_NAMESPACE, workspace_id,
        )

    # 4) Heimdall: новый владелец — owner
    await upsert_grant(heimdall_connection, new_owner_id, workspace_id, OWNER_ROLE_ID)

async def main() -> None:
    yggdrasil_url = get_database_url(("YGGDRASIL_DATABASE_URL",), DEFAULT_YGGDRASIL_URL)
    heimdall_url = get_database_url(("HEIMDALL_DATABASE_URL", "AUTH_DATABASE_URL"), DEFAULT_HEIMDALL_URL)

    if not WORKSPACE_IDS:
        print("ERROR: заполните WORKSPACE_IDS")
        sys.exit(2)
    if not NEW_OWNER_EMAIL or "@" not in NEW_OWNER_EMAIL:
        print("ERROR: заполните NEW_OWNER_EMAIL")
        sys.exit(2)

    yggdrasil_connection = await asyncpg.connect(yggdrasil_url)
    heimdall_connection = await asyncpg.connect(heimdall_url)
    try:
        new_owner_id = await resolve_user_id(yggdrasil_connection, NEW_OWNER_EMAIL)
        print(f"новый владелец: {NEW_OWNER_EMAIL} (user_id={new_owner_id})")
        print(f"режим: {'DRY RUN — ничего не будет изменено' if DRY_RUN else 'APPLY'}")
        print()

        failed = 0
        for workspace_id in WORKSPACE_IDS:
            try:
                ws = await get_workspace_state(yggdrasil_connection, workspace_id)
            except ValueError as error:
                print(f"[SKIP] workspace_id={workspace_id}: {error}")
                failed += 1
                continue

            old_owner_id = ws["owner_id"]
            if old_owner_id == new_owner_id:
                print(f"[SKIP] workspace_id={workspace_id} ({ws['name']}): владелец и так {NEW_OWNER_EMAIL}")
                continue

            old_owner_alive = await user_exists(yggdrasil_connection, old_owner_id)
            members = await get_members(yggdrasil_connection, workspace_id)
            member_roles = {m["user_id"]: m["role_id"] for m in members}
            old_grant = await get_grant(heimdall_connection, old_owner_id, workspace_id)
            new_grant = await get_grant(heimdall_connection, new_owner_id, workspace_id)

            print(f"workspace_id={workspace_id} name={ws['name']!r} type={ws['type']}")
            alive_label = "активен" if old_owner_alive else "УДАЛЁН со стенда (zombie)"
            print(f"  текущий владелец:  user_id={old_owner_id} — {alive_label}")
            print(f"    yggdrasil role={member_roles.get(old_owner_id)}, heimdall grant={old_grant}")
            print(f"  новый владелец:    user_id={new_owner_id} ({NEW_OWNER_EMAIL})")
            print(f"    yggdrasil role={member_roles.get(new_owner_id)}, heimdall grant={new_grant}")
            if new_owner_id not in member_roles:
                print("  [INFO] новый владелец ещё не в workspace_members — запись будет создана с ролью owner")
            if old_owner_alive:
                print("  план: старый владелец понизится до member, новый получит owner")
            else:
                print("  план: zombie-записи старого владельца будут удалены (workspace_members + access_grants), новый получит owner")

            if DRY_RUN:
                print("  DRY RUN: изменения не применены")
                print()
                continue

            async with yggdrasil_connection.transaction(), heimdall_connection.transaction():
                await transfer(
                    yggdrasil_connection, heimdall_connection,
                    workspace_id, old_owner_id, new_owner_id, old_owner_alive,
                )
            print("  [OK] владелец передан")
            print()

        print(f"итого: обработано={len(WORKSPACE_IDS) - failed} skipped/failed={failed}")
        if failed:
            sys.exit(1)
    finally:
        await yggdrasil_connection.close()
        await heimdall_connection.close()

if __name__ == "__main__":
    asyncio.run(main())
'''

_MASS_ADD_TEMPLATE = '''

MEMBER_ROLE_ID = "workspace.member"
WORKSPACE_IDS = [
__IDS__
]

def get_required_database_url(variable_name: str) -> str:
    value = os.environ.get(variable_name)
    if not value:
        raise RuntimeError(f"Required environment variable is not set: {variable_name}")
    return value

async def get_all_user_ids(connection: asyncpg.Connection) -> set[str]:
    rows = await connection.fetch(
        """
        SELECT id::text AS id
        FROM users
        ORDER BY id
        """
    )
    return {row["id"] for row in rows}

async def get_existing_workspace_member_ids(
    connection: asyncpg.Connection,
    workspace_id: str,
) -> set[str]:
    rows = await connection.fetch(
        """
        SELECT user_id::text AS user_id
        FROM workspace_members
        WHERE workspace_id = $1
        ORDER BY user_id
        """,
        workspace_id,
    )
    return {row["user_id"] for row in rows}

async def ensure_workspace_is_active(
    connection: asyncpg.Connection,
    workspace_id: str,
) -> None:
    exists = await connection.fetchval(
        """
        SELECT EXISTS(
            SELECT 1
            FROM workspaces
            WHERE id = $1
              AND status <> 'archived'
              AND trash_state IS NULL
        )
        """,
        workspace_id,
    )
    if not exists:
        raise ValueError(f"Workspace is missing, archived, or trashed: {workspace_id}")

async def insert_missing_workspace_members(
    connection: asyncpg.Connection,
    workspace_id: str,
    user_ids: list[str],
) -> int:
    if not user_ids:
        return 0

    async with connection.transaction():
        rows = await connection.fetch(
            """
            INSERT INTO workspace_members (workspace_id, user_id, role_id)
            SELECT $1::uuid, input.user_id::uuid, $3
            FROM unnest($2::text[]) AS input(user_id)
            ON CONFLICT (workspace_id, user_id) DO NOTHING
            RETURNING user_id::text AS user_id
            """,
            workspace_id,
            user_ids,
            MEMBER_ROLE_ID,
        )
        await connection.execute(
            """
            INSERT INTO workspace_favorites (workspace_id, user_id)
            SELECT $1::uuid, input.user_id::uuid
            FROM unnest($2::text[]) AS input(user_id)
            ON CONFLICT (workspace_id, user_id) DO NOTHING
            RETURNING user_id::text AS user_id
            """,
            workspace_id,
            user_ids,
        )
    return len(rows)

async def insert_missing_access_grants(
    heimdall_connection: asyncpg.Connection,
    workspace_id: str,
    user_ids: list[str],
) -> int:
    if not user_ids:
        return 0

    rows = await heimdall_connection.fetch(
        """
        INSERT INTO access_grants (user_id, namespace, resource_id, role_id)
        SELECT input.user_id, $1, $2, $4
        FROM unnest($3::text[]) AS input(user_id)
        ON CONFLICT (user_id, namespace, resource_id) DO NOTHING
        RETURNING user_id
        """,
        WORKSPACE_NAMESPACE,
        workspace_id,
        user_ids,
        MEMBER_ROLE_ID,
    )
    return len(rows)

async def main() -> None:
    yggdrasil_url = os.environ.get("YGGDRASIL_DATABASE_URL",
                                   __YGG__)
    heimdall_url = os.environ.get("HEIMDALL_DATABASE_URL",
                                  __HM__)

    yggdrasil_connection = await asyncpg.connect(yggdrasil_url)
    heimdall_connection = await asyncpg.connect(heimdall_url)
    try:
        all_user_ids = await get_all_user_ids(yggdrasil_connection)
        if not all_user_ids:
            print("No users found in users table")
            return

        for workspace_id in WORKSPACE_IDS:
            try:
                await ensure_workspace_is_active(yggdrasil_connection, workspace_id)
            except ValueError:
                continue

            existing_user_ids = await get_existing_workspace_member_ids(
                yggdrasil_connection,
                workspace_id,
            )
            missing_user_ids = sorted(all_user_ids - existing_user_ids)
            print(
                "workspace diff "
                f"workspace_id={workspace_id} "
                f"users_total={len(all_user_ids)} "
                f"members_existing={len(existing_user_ids)} "
                f"members_missing={len(missing_user_ids)} "
                f"members_to_insert={len(missing_user_ids)}"
            )

            inserted_count = await insert_missing_workspace_members(
                yggdrasil_connection,
                workspace_id=workspace_id,
                user_ids=missing_user_ids,
            )
            print(
                "workspace_members inserted "
                f"workspace_id={workspace_id} "
                f"role_id={MEMBER_ROLE_ID} "
                f"count={inserted_count}"
            )
            grants_inserted_count = await insert_missing_access_grants(
                heimdall_connection,
                workspace_id=workspace_id,
                user_ids=missing_user_ids,
            )
            print(
                "access_grants inserted "
                f"workspace_id={workspace_id} "
                f"namespace={WORKSPACE_NAMESPACE} "
                f"role_id={MEMBER_ROLE_ID} "
                f"count={grants_inserted_count}"
            )
    finally:
        await yggdrasil_connection.close()
        await heimdall_connection.close()

if __name__ == "__main__":
    asyncio.run(main())
'''

_ADD_USER_TEMPLATE = '''from datetime import datetime, timezone

MEMBER_ROLE_ID = "workspace.member"

WORKSPACE_IDS = [
__IDS__
]
USER_EMAILS = [
__EMAILS__
]
# True — только показать, что будет сделано, ничего не менять
DRY_RUN = __DRY__

DEFAULT_YGGDRASIL_URL = __YGG__
DEFAULT_HEIMDALL_URL = __HM__

async def get_invite_status(heimdall_connection: asyncpg.Connection, email: str) -> str:
    row = await heimdall_connection.fetchrow(
        """
        SELECT status, expires_at
        FROM invite_tokens
        WHERE email_normalized = $1
        ORDER BY updated_at DESC
        LIMIT 1
        """,
        email.lower(),
    )
    if row is None:
        return "пользователь не приглашён"
    now = datetime.now(timezone.utc)
    expires_at = row["expires_at"]
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if row["status"] == "pending" and expires_at > now:
        return f"приглашение отправлено, не принято, ссылка живёт до {expires_at:%Y-%m-%d %H:%M} UTC"
    if expires_at <= now:
        return f"ссылка истекла {expires_at:%Y-%m-%d %H:%M} UTC, нужно перевыслать приглашение"
    return f"токен в статусе {row['status']}, пользователя в провайдере нет"

async def add_member(
    yggdrasil_connection: asyncpg.Connection,
    heimdall_connection: asyncpg.Connection,
    workspace_id: str,
    user_id: str,
) -> None:
    # 1) Yggdrasil: workspace_members
    await yggdrasil_connection.execute(
        """
        INSERT INTO workspace_members (workspace_id, user_id, role_id)
        VALUES ($1, $2, $3)
        ON CONFLICT (workspace_id, user_id) DO NOTHING
        """,
        workspace_id, user_id, MEMBER_ROLE_ID,
    )

    # 2) Yggdrasil: workspace_favorites
    await yggdrasil_connection.execute(
        """
        INSERT INTO workspace_favorites (workspace_id, user_id)
        VALUES ($1, $2)
        ON CONFLICT (workspace_id, user_id) DO NOTHING
        """,
        workspace_id, user_id,
    )

    # 3) Heimdall: access_grants
    await heimdall_connection.execute(
        """
        INSERT INTO access_grants (user_id, namespace, resource_id, role_id)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (user_id, namespace, resource_id) DO NOTHING
        """,
        user_id, WORKSPACE_NAMESPACE, workspace_id, MEMBER_ROLE_ID,
    )

async def main() -> None:
    yggdrasil_url = get_database_url(("YGGDRASIL_DATABASE_URL",), DEFAULT_YGGDRASIL_URL)
    heimdall_url = get_database_url(("HEIMDALL_DATABASE_URL", "AUTH_DATABASE_URL"), DEFAULT_HEIMDALL_URL)

    if not WORKSPACE_IDS:
        print("ERROR: заполните WORKSPACE_IDS")
        sys.exit(2)
    if not USER_EMAILS or any("@" not in email for email in USER_EMAILS):
        print("ERROR: заполните USER_EMAILS (список email)")
        sys.exit(2)

    yggdrasil_connection = await asyncpg.connect(yggdrasil_url)
    heimdall_connection = await asyncpg.connect(heimdall_url)
    try:
        print(f"пользователи: {', '.join(USER_EMAILS)}")
        print(f"режим: {'DRY RUN — ничего не будет изменено' if DRY_RUN else 'APPLY'}")
        print()

        added = 0
        already = 0
        planned = 0
        ws_errors = 0
        missing = []
        for workspace_id in WORKSPACE_IDS:
            try:
                ws = await get_workspace_state(yggdrasil_connection, workspace_id)
            except ValueError as error:
                print(f"[SKIP] workspace_id={workspace_id}: {error}")
                ws_errors += 1
                continue

            print(f"workspace_id={workspace_id} name={ws['name']!r} type={ws['type']}")

            for email in USER_EMAILS:
                try:
                    user_id = await resolve_user_id(yggdrasil_connection, email)
                except ValueError as error:
                    print(f"  [SKIP] {email}: {error}")
                    if email not in missing:
                        missing.append(email)
                    continue

                existing_role = await get_member_role(yggdrasil_connection, workspace_id, user_id)
                existing_grant = await get_grant(heimdall_connection, user_id, workspace_id)

                print(f"  пользователь: user_id={user_id} ({email})")
                print(f"    yggdrasil role={existing_role}, heimdall grant={existing_grant}")

                if existing_role == MEMBER_ROLE_ID and existing_grant == MEMBER_ROLE_ID:
                    print("  [SKIP] пользователь уже участник с ролью member")
                    already += 1
                    print()
                    continue

                if DRY_RUN:
                    print("  план: добавить в workspace_members, workspace_favorites, access_grants (role=member)")
                    print("  DRY RUN: изменения не применены")
                    planned += 1
                    print()
                    continue

                async with yggdrasil_connection.transaction(), heimdall_connection.transaction():
                    await add_member(
                        yggdrasil_connection, heimdall_connection,
                        workspace_id, user_id,
                    )
                print("  [OK] пользователь добавлен в пространство")
                added += 1
                print()

        if DRY_RUN:
            print(f"итого: пространств={len(WORKSPACE_IDS)}, к добавлению={planned}, уже участники={already}, ошибок пространств={ws_errors}")
        else:
            print(f"итого: пространств={len(WORKSPACE_IDS)}, добавлено={added}, уже участники={already}, ошибок пространств={ws_errors}")
        if missing:
            print(f"не найдено пользователей={len(missing)}: {', '.join(missing)}")
            for email in missing:
                try:
                    status = await get_invite_status(heimdall_connection, email)
                except Exception as error:
                    status = f"статус приглашения не удалось определить ({error})"
                print(f"  {email}: {status}")
        if ws_errors or missing:
            sys.exit(1)
    finally:
        await yggdrasil_connection.close()
        await heimdall_connection.close()

if __name__ == "__main__":
    asyncio.run(main())
'''

_REMOVE_USER_TEMPLATE = '''WORKSPACE_IDS = [
__IDS__
]
USER_EMAILS = [
__EMAILS__
]
# Дополнительные user_id (UUID), которые нужно проверить/удалить.
# Заполните, если пользователь не находится по email (осиротевшие гранты).
EXTRA_USER_IDS = [
__EXTRA_IDS__
]
# True — только показать, что будет сделано, ничего не менять
DRY_RUN = __DRY__
DEFAULT_YGGDRASIL_URL = __YGG__
DEFAULT_HEIMDALL_URL = __HM__
async def fetch_user_ids_by_email(connection: asyncpg.Connection, email: str) -> list[str]:
    """Все id из users по email (в одной таблице может быть несколько строк)."""
    try:
        rows = await connection.fetch(
            "SELECT id::text FROM users WHERE lower(email) = lower($1)", email
        )
    except Exception:
        return []
    return [r["id"] for r in rows]
async def resolve_user_ids(
    yggdrasil_connection: asyncpg.Connection,
    heimdall_connection: asyncpg.Connection,
    email: str,
) -> list[str]:
    """Все user_id по email: из users (yggdrasil) и из users (heimdall_auth, если есть).
    Плюс — все user_id, у которых есть access_grants на эти пространства,
    и email которых совпадает в любой из таблиц users (ловит legacy-гранты,
    привязанные к id, который по email не находится)."""
    ids = []
    for conn in (yggdrasil_connection, heimdall_connection):
        for uid in await fetch_user_ids_by_email(conn, email):
            if uid not in ids:
                ids.append(uid)
    # Доп. источник: user_id из access_grants по пространствам.
    try:
        rows = await heimdall_connection.fetch(
            """
            SELECT DISTINCT user_id::text AS uid
            FROM access_grants
            WHERE namespace = $1 AND resource_id = ANY($2)
            """,
            WORKSPACE_NAMESPACE, WORKSPACE_IDS,
        )
        for r in rows:
            uid = r["uid"]
            if uid in ids:
                continue
            # Проверяем, что это наш email (в любой из таблиц users).
            is_ours = False
            for conn in (yggdrasil_connection, heimdall_connection):
                try:
                    row = await conn.fetchrow(
                        "SELECT 1 FROM users WHERE id = $1 AND lower(email) = lower($2)",
                        uid, email,
                    )
                except Exception:
                    row = None
                if row is not None:
                    is_ours = True
                    break
            if is_ours:
                ids.append(uid)
    except Exception:
        pass
    return ids
async def remove_member(
    yggdrasil_connection: asyncpg.Connection,
    heimdall_connection: asyncpg.Connection,
    workspace_id: str,
    user_id: str,
) -> None:
    # 1) Yggdrasil: workspace_members
    await yggdrasil_connection.execute(
        """
        DELETE FROM workspace_members
        WHERE workspace_id = $1 AND user_id = $2
        """,
        workspace_id, user_id,
    )
    # 2) Yggdrasil: workspace_favorites
    await yggdrasil_connection.execute(
        """
        DELETE FROM workspace_favorites
        WHERE workspace_id = $1 AND user_id = $2
        """,
        workspace_id, user_id,
    )
    # 3) Heimdall: access_grants
    await heimdall_connection.execute(
        """
        DELETE FROM access_grants
        WHERE user_id = $1
          AND namespace = $2
          AND resource_id = $3
        """,
        user_id, WORKSPACE_NAMESPACE, workspace_id,
    )
async def main() -> None:
    yggdrasil_url = get_database_url(("YGGDRASIL_DATABASE_URL",), DEFAULT_YGGDRASIL_URL)
    heimdall_url = get_database_url(("HEIMDALL_DATABASE_URL", "AUTH_DATABASE_URL"), DEFAULT_HEIMDALL_URL)
    if not WORKSPACE_IDS:
        print("ERROR: заполните WORKSPACE_IDS")
        sys.exit(2)
    if not USER_EMAILS or any("@" not in email for email in USER_EMAILS):
        print("ERROR: заполните USER_EMAILS (список email)")
        sys.exit(2)
    import uuid as _uuid
    for uid in EXTRA_USER_IDS:
        try:
            _uuid.UUID(uid)
        except ValueError:
            print(f"ERROR: EXTRA_USER_IDS содержит не-UUID: {uid}")
            sys.exit(2)
    yggdrasil_connection = await asyncpg.connect(yggdrasil_url)
    heimdall_connection = await asyncpg.connect(heimdall_url)
    try:
        print(f"пользователи: {', '.join(USER_EMAILS)}")
        print(f"режим: {'DRY RUN — ничего не будет изменено' if DRY_RUN else 'APPLY'}")
        print()
        removed = 0
        not_member = 0
        planned = 0
        owners = 0
        ws_errors = 0
        missing = []
        seen_uids = set()
        for workspace_id in WORKSPACE_IDS:
            try:
                ws = await get_workspace_state(yggdrasil_connection, workspace_id)
            except ValueError as error:
                print(f"[SKIP] workspace_id={workspace_id}: {error}")
                ws_errors += 1
                continue
            print(f"workspace_id={workspace_id} name={ws['name']!r} type={ws['type']}")
            for email in USER_EMAILS:
                user_ids = await resolve_user_ids(yggdrasil_connection, heimdall_connection, email)
                for uid in user_ids:
                    seen_uids.add(uid)
                if not user_ids:
                    print(f"  [SKIP] {email}: пользователь не найден в users (yggdrasil/heimdall_auth)")
                    if email not in missing:
                        missing.append(email)
                    continue
                # Защита: владельца удалить нельзя — сначала смените владельца.
                if ws["owner_id"] in user_ids:
                    print(f"  [SKIP] {email}: пользователь — владелец пространства, сначала смените владельца")
                    owners += 1
                    print()
                    continue
                print(f"  пользователь: {email} (user_id: {', '.join(user_ids)})")
                any_role = None
                any_grant = None
                for user_id in user_ids:
                    role = await get_member_role(yggdrasil_connection, workspace_id, user_id)
                    grant = await get_grant(heimdall_connection, user_id, workspace_id)
                    print(f"    user_id={user_id}: yggdrasil role={role}, heimdall grant={grant}")
                    if role is not None:
                        any_role = role
                    if grant is not None:
                        any_grant = grant
                if any_role is None and any_grant is None:
                    print("  [SKIP] пользователь не является участником пространства")
                    not_member += 1
                    print()
                    continue
                if DRY_RUN:
                    print("  план: удалить из workspace_members, workspace_favorites, access_grants (по всем user_id)")
                    print("  DRY RUN: изменения не применены")
                    planned += 1
                    print()
                    continue
                async with yggdrasil_connection.transaction(), heimdall_connection.transaction():
                    for user_id in user_ids:
                        await remove_member(
                            yggdrasil_connection, heimdall_connection,
                            workspace_id, user_id,
                        )
                print("  [OK] пользователь удалён из пространства")
                removed += 1
                print()
            # Дополнительные user_id (вручную из чекера)
            for extra_uid in EXTRA_USER_IDS:
                if extra_uid in seen_uids:
                    continue
                seen_uids.add(extra_uid)
                if ws["owner_id"] == extra_uid:
                    print(f"  [SKIP] user_id={extra_uid}: владелец пространства, сначала смените владельца")
                    owners += 1
                    print()
                    continue
                role = await get_member_role(yggdrasil_connection, workspace_id, extra_uid)
                grant = await get_grant(heimdall_connection, extra_uid, workspace_id)
                print(f"  пользователь: (user_id={extra_uid}, из EXTRA_USER_IDS)")
                print(f"    yggdrasil role={role}, heimdall grant={grant}")
                if role is None and grant is None:
                    print("  [SKIP] записей нет")
                    not_member += 1
                    print()
                    continue
                if DRY_RUN:
                    print("  план: удалить из workspace_members, workspace_favorites, access_grants")
                    print("  DRY RUN: изменения не применены")
                    planned += 1
                    print()
                    continue
                async with yggdrasil_connection.transaction(), heimdall_connection.transaction():
                    await remove_member(
                        yggdrasil_connection, heimdall_connection,
                        workspace_id, extra_uid,
                    )
                print("  [OK] пользователь удалён из пространства")
                removed += 1
                print()
        if DRY_RUN:
            print(f"итого: пространств={len(WORKSPACE_IDS)}, к удалению={planned}, не участники={not_member}, владельцев={owners}, ошибок пространств={ws_errors}")
        else:
            print(f"итого: пространств={len(WORKSPACE_IDS)}, удалено={removed}, не участники={not_member}, владельцев={owners}, ошибок пространств={ws_errors}")
        if missing:
            print(f"не найдено пользователей={len(missing)}: {', '.join(missing)}")
        if ws_errors or missing or owners:
            sys.exit(1)
    finally:
        await yggdrasil_connection.close()
        await heimdall_connection.close()
if __name__ == "__main__":
    asyncio.run(main())
'''

_LIST_WORKSPACES_TEMPLATE = '''

async def main() -> None:
    yggdrasil_url = get_database_url(("YGGDRASIL_DATABASE_URL",), __YGG__)

    yggdrasil_connection = await asyncpg.connect(yggdrasil_url)
    try:
        rows = await yggdrasil_connection.fetch(
            """
            SELECT id::text AS id, name, status, trash_state
            FROM workspaces
            ORDER BY name
            """
        )
        if not rows:
            print("Пространств на стенде нет.")
            return
        print(f"Всего пространств: {len(rows)}")
        print()
        for row in rows:
            if row["status"] == "archived":
                mark = " [АРХИВ]"
            elif row["trash_state"] is not None:
                mark = " [В КОРЗИНЕ]"
            else:
                mark = ""
            print(f"{row['name']}{mark}")
            print(f"  ID: {row['id']}")
            print()
    finally:
        await yggdrasil_connection.close()

if __name__ == "__main__":
    asyncio.run(main())
'''


def _ids_block(workspace_ids):
    return _json_list_block(workspace_ids)


def _emails_block(user_emails):
    return _json_list_block(user_emails)


def _json_list_block(values):
    """Блок JSON-значений для вставки в список Python: по одному на строку, отступ 4."""
    return ",\n".join(f"    {json.dumps(v)}" for v in values)


def _render_script(template, **replacements):
    """Собирает скрипт: общий блок хелперов + шаблон + подстановка плейсхолдеров __KEY__."""
    result = _SCRIPT_COMMON_HELPERS + template
    for key, value in replacements.items():
        result = result.replace(f"__{key}__", value)
    return result


def generate_change_owner_script(workspace_ids, new_owner_email, dry_run, ygg_url, hm_url):
    return _render_script(_CHANGE_OWNER_TEMPLATE,
                          IDS=_ids_block(workspace_ids),
                          EMAIL=json.dumps(new_owner_email),
                          DRY="True" if dry_run else "False",
                          YGG=json.dumps(ygg_url),
                          HM=json.dumps(hm_url))


def generate_mass_add_script(workspace_ids, ygg_url, hm_url):
    return _render_script(_MASS_ADD_TEMPLATE,
                          IDS=_ids_block(workspace_ids),
                          YGG=json.dumps(ygg_url),
                          HM=json.dumps(hm_url))


def generate_add_user_script(workspace_ids, user_emails, dry_run, ygg_url, hm_url):
    return _render_script(_ADD_USER_TEMPLATE,
                          IDS=_ids_block(workspace_ids),
                          EMAILS=_emails_block(user_emails),
                          DRY="True" if dry_run else "False",
                          YGG=json.dumps(ygg_url),
                          HM=json.dumps(hm_url))


def generate_remove_user_script(workspace_ids, user_emails, dry_run, ygg_url, hm_url, extra_user_ids=None):
    extra_ids = extra_user_ids or []
    extra_block = _json_list_block(extra_ids) if extra_ids else "    # (пусто — не нужно)"
    return _render_script(_REMOVE_USER_TEMPLATE,
                          EXTRA_IDS=extra_block,
                          IDS=_ids_block(workspace_ids),
                          EMAILS=_emails_block(user_emails),
                          DRY="True" if dry_run else "False",
                          YGG=json.dumps(ygg_url),
                          HM=json.dumps(hm_url))


def generate_list_workspaces_script(ygg_url):
    return _render_script(_LIST_WORKSPACES_TEMPLATE, YGG=json.dumps(ygg_url))

# --- Починить роли на стенде (heimdall_auth) ---
_FIX_EMPTY_ROLES_TEMPLATE = '''# Роль «Участник» (global role id в heimdall_auth)
MEMBER_ROLE_ID = "6441ce25-27e5-436a-ae2d-374d080dcab1"
# Исключения: тестовый аккаунт и внешние пользователи не трогаем
EXCLUDE_EMAILS = ["cp-test@gigab2b.ru"]
EXTERNAL_PREFIX = "ext-"
# True — только показать, кто без ролей, ничего не менять
DRY_RUN = __DRY__
DEFAULT_HEIMDALL_URL = __HM__
async def main() -> None:
    heimdall_url = get_database_url(("HEIMDALL_DATABASE_URL", "AUTH_DATABASE_URL"), DEFAULT_HEIMDALL_URL)
    connection = await asyncpg.connect(heimdall_url)
    try:
        rows = await connection.fetch(
            """
            SELECT pu.provider_user_id::text AS user_id, pu.email_normalized AS email
            FROM provider_users pu
            WHERE pu.email_normalized IS NOT NULL
              AND btrim(pu.email_normalized) <> ''
              AND lower(pu.email_normalized) <> 'cp-test@gigab2b.ru'
              AND NOT EXISTS (
                  SELECT 1
                  FROM user_global_roles ugr
                  WHERE ugr.user_id = pu.provider_user_id
              )
            ORDER BY pu.email_normalized
            """
        )
        # Фильтр ext-* на стороне Python: в DRY RUN показываем всех, но помечаем
        candidates = []
        skipped_external = 0
        for row in rows:
            email = (row["email"] or "").lower()
            if email.startswith(EXTERNAL_PREFIX):
                skipped_external += 1
                continue
            candidates.append(dict(row))
        print(f"Пользователей без глобальной роли: {len(rows)}")
        if skipped_external:
            print(f"Среди них внешних (ext-*), которые НЕ получат роль: {skipped_external}")
        if not candidates:
            print("Никому выдавать роль не нужно.")
            return
        print()
        for row in candidates:
            print(f"  {row['email']}")
            print(f"    user_id: {row['user_id']}")
        print()
        if DRY_RUN:
            print(f"[DRY RUN] Будут добавлены записи в user_global_roles: {len(candidates)} шт.")
            print("[DRY RUN] Ничего не изменено. Для боевого запуска запустите скрипт с DRY_RUN=False.")
            return
        inserted = 0
        for row in candidates:
            res = await connection.execute(
                """
                INSERT INTO user_global_roles (user_id, role_id, source, assigned_by)
                VALUES ($1, $2, 'local', 'migration:workspaces-access-backfill')
                ON CONFLICT (user_id, role_id) DO NOTHING
                """,
                row["user_id"], MEMBER_ROLE_ID,
            )
            if res == "INSERT 1":
                inserted += 1
        print(f"Добавлено записей в user_global_roles: {inserted} из {len(candidates)}.")
    finally:
        await connection.close()
if __name__ == "__main__":
    asyncio.run(main())
'''
def generate_fix_empty_roles_script(dry_run, hm_url):
    return _render_script(_FIX_EMPTY_ROLES_TEMPLATE,
                          DRY="True" if dry_run else "False",
                          HM=json.dumps(hm_url))


def build_db_url(user, host, folder, db):
    # Шаблон с полным DNS-суффиксом неймспейса стенда (неймспейс = имя папки стенда).
    return f"postgresql://{user}:<ПАРОЛЬ>@{host}.{folder}.svc.cluster.local:5432/{db}"

# --- Управление пространствами: ArgoCD ---
def _argocd_base(zone_code):
    return f"https://argocd-{zone_code}.cowork.ru"


def open_pod_exec(zone_code, stand_name, app_suffix, app_template=None, argocd_base=None):
    """Страница приложения yggdrasil/heimdall в ArgoCD; если все поды в одном приложении — браузер открывается один раз."""
    if app_template:
        app_path = app_template.format(stand_name=stand_name, app_suffix=app_suffix)
        suffix_dependent = "{app_suffix}" in app_template
    else:
        app_path = f"{stand_name}/{stand_name}-{app_suffix}"
        suffix_dependent = True
    base = argocd_base or _argocd_base(zone_code)
    if suffix_dependent:
        open_url(f"{base}/applications/{app_path}?resource=")
        print_info(f"В списке приложения выберите под {app_suffix}* и откройте вкладку exec.")
    else:
        if not getattr(open_pod_exec, "_opened", None):
            open_url(f"{base}/applications/{app_path}?resource=")
            open_pod_exec._opened = True
            print_info(f"Приложение {app_path} открыто — выберите под {app_suffix}* и откройте вкладку exec.")
        else:
            print_info(f"В уже открытом приложении {app_path} переключитесь на под {app_suffix}* (вкладка exec).")


def open_workspace_exec(zone_code, script_content, script_name, argocd_base=None):
    """Скрипт в exec/ + страница приложения ArgoCD (под debug-ubuntu выбирается вручную)."""
    EXEC_DIR.mkdir(parents=True, exist_ok=True)
    out = EXEC_DIR / script_name
    out.write_text(script_content, encoding="utf-8")
    print_info(f"Скрипт сохранён: {out}")
    base = argocd_base or _argocd_base(zone_code)
    open_url(f"{base}/applications/{ARGOCD_APP}?resource=")
    print_info(f"В списке приложения выберите под {DEBUG_POD_PREFIX}* (вкладка exec), "
               f"скопируйте содержимое {script_name} в веб-терминал и запустите: python3 {script_name}")


# --- Управление пространствами: ввод параметров ---
def prompt_workspace_ids():
    print_info("Введите ID пространств через запятую (ID — из URL /workspaces/<ID>):")
    raw = input(f"{action_label()} ID: ").strip()
    return [x.strip() for x in re.split(r"[,\s]+", raw) if x.strip()]


def _extract_dsn(raw):
    """DSN из строки 'ИМЯ_ПЕРЕМЕННОЙ=postgresql://...'; чистый DSN принимается как есть."""
    value = raw.strip()
    if not value:
        return None
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):  # скопировано с кавычками
        value = value[1:-1].strip()
    if not value.startswith(("postgresql://", "postgres://")) and "=" in value:  # префикс ИМЯ_ПЕРЕМЕННОЙ=
        value = value.split("=", 1)[1].strip()
    if not (value.startswith("postgresql://") or value.startswith("postgres://")):
        return None
    return value


def _expand_dns_suffix(url, folder):
    """Короткому хосту DSN дополняется суффикс <stand_name>.svc.cluster.local (под отдаёт имя сервиса без неймспейса)."""
    if not folder:
        return url
    m = re.match(r"^(postgresql?://[^@/]+@)([^:/]+)(:\d+)?(/.*)$", url)
    if not m:
        return url
    scheme_user, host, port, path = m.groups()
    if ".svc.cluster.local" in host:
        return url
    new_host = f"{host}.{folder}.svc.cluster.local"
    return f"{scheme_user}{new_host}{port or ''}{path}"


def _resolve_db_url(raw, user, host, db, folder, var_name):
    """Разбирает введённую строку в DSN; при неудаче — шаблон с <ПАРОЛЬ>."""
    url = _extract_dsn(raw)
    if raw and url is None:
        print_warn("Строка не распознана (ожидается 'ИМЯ=postgresql://...'). Вставлен шаблон с <ПАРОЛЬ>.")
    if url is None:
        url = build_db_url(user, host, folder, db)
        if not raw:
            print_warn(f"{var_name} не задан — вставлен шаблон с <ПАРОЛЬ>, заполните в скрипте.")
    else:
        url = _expand_dns_suffix(url, folder)
    return url


class DsnCache:
    """Кэш DSN по стендам (файл ~/.gigafinder/dsn-cache.json).

    Общий фоллбек всех операций: «кэш → ArgoCD → ввод» и сохранение
    распознанного DSN обратно в кэш.
    """

    def __init__(self, path):
        self.path = Path(path)
        self.data = {}
        try:
            with open(self.path, encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                self.data = loaded
        except (OSError, json.JSONDecodeError):
            pass

    def save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except OSError as error:
            print_warn(f"Не удалось сохранить кэш DSN: {error}")

    def entry(self, folder):
        return self.data.get(folder) or {}

    def offer_cached(self, folder, key, label):
        """Предложить значение из кэша; True — использовать, False — ввести заново."""
        value = self.entry(folder).get(key)
        if not value:
            return False
        saved_at = self.entry(folder).get("saved_at", "дата неизвестна")
        print_info(f"Найден кэш DSN {label} для стенда {folder} (сохранён {saved_at}).")
        choice = input(f"{action_label()} Enter — использовать из кэша, d — ввести заново: ").strip().lower()
        if choice != "d":
            print_info(f"Использую DSN {label} из кэша для стенда {folder}.")
            return True
        return False

    def store(self, folder, note, **values):
        """Сохранить значения в кэш (только распознанные DSN) и показать подсказку."""
        if any("<ПАРОЛЬ>" in v for v in values.values()):
            return
        entry = dict(self.entry(folder))
        entry.update(values)
        entry["saved_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        self.data[folder] = entry
        self.save()
        print_info(f"{note} в кэш ({self.path}) — в следующий раз вводить не придётся.")


def _stand_name(selected):
    """Имя стенда для отображения: stand_name или folder как фоллбек."""
    return selected.get("stand_name") or selected.get("folder", "")


def prompt_db_urls(selected, zone, need_heimdall=True):
    folder = selected.get("folder", "")
    stand_name = _stand_name(selected)

    cache = DSN_CACHE
    cached = cache.entry(folder)
    cached_ygg = cached.get("yggdrasil")
    cached_hm = cached.get("heimdall")
    if cached_ygg and (not need_heimdall or cached_hm):
        saved_at = cached.get("saved_at", "дата неизвестна")
        print_info(f"Найден кэш DSN для стенда {folder} (сохранён {saved_at}).")
        choice = input(f"{action_label()} Enter — использовать из кэша, d — ввести заново: ").strip().lower()
        if choice != "d":
            ygg = cached_ygg
            hm = cached_hm if need_heimdall else None
            print_info(f"Использую DSN из кэша для стенда {folder}.")
            return ygg, hm

    open_pod_exec._opened = False
    print_info("Открываю приложение yggdrasil в ArgoCD — скопируйте YGGDRASIL_DATABASE_URL.")
    open_pod_exec(zone["code"], stand_name, "yggdrasil", zone.get("app_template"), zone.get("argocd_base"))
    ygg_raw = input(f"{action_label()} Вставьте строку целиком (в поде: {colorize('env | grep YGGDRASIL_DATABASE_URL', CYAN)}): ").strip()

    hm_raw = ""
    if need_heimdall:
        print_info("Открываю приложение heimdall в ArgoCD — скопируйте AUTH_DATABASE_URL.")
        open_pod_exec(zone["code"], stand_name, "heimdall", zone.get("app_template"), zone.get("argocd_base"))
        hm_raw = input(f"{action_label()} Вставьте строку целиком (в поде: {colorize('env | grep AUTH_DATABASE_URL', CYAN)}): ").strip()

    ygg = _resolve_db_url(ygg_raw, "yggdrasil", "yggdrasil-postgres-rw", "yggdrasil", folder, "YGGDRASIL_DATABASE_URL")
    hm = None
    if need_heimdall:
        hm = _resolve_db_url(hm_raw, "heimdall_auth", "heimdall-auth-postgres-rw", "heimdall_auth", folder, "HEIMDALL_DATABASE_URL")

    if "<ПАРОЛЬ>" not in ygg and "<ПАРОЛЬ>" not in (hm or ""):  # в кэш — только распознанные DSN
        values = {"yggdrasil": ygg}
        if need_heimdall:
            values["heimdall"] = hm
        cache.store(folder, "DSN сохранены", **values)

    return ygg, hm


def prompt_heimdall_url(selected, zone):
    """Запрашивает DSN heimdall_auth (кэш → ArgoCD → ввод); возвращает URL."""
    folder = selected.get("folder", "")
    stand_name = _stand_name(selected)
    cache = DSN_CACHE
    if cache.offer_cached(folder, "heimdall", "heimdall"):
        return cache.entry(folder)["heimdall"]
    open_pod_exec._opened = False
    print_info("Открываю приложение heimdall в ArgoCD — скопируйте AUTH_DATABASE_URL.")
    open_pod_exec(zone["code"], stand_name, "heimdall", zone.get("app_template"), zone.get("argocd_base"))
    hm_raw = input(f"{action_label()} Вставьте строку целиком (в поде: {colorize('env | grep AUTH_DATABASE_URL', CYAN)}): ").strip()
    hm = _resolve_db_url(hm_raw, "heimdall_auth", "heimdall-auth-postgres-rw", "heimdall_auth", folder, "HEIMDALL_DATABASE_URL")
    if "<ПАРОЛЬ>" not in hm:
        cache.store(folder, "DSN heimdall сохранён", heimdall=hm)
    return hm


DSN_CACHE = DsnCache(DSN_CACHE_PATH)


def resolve_zone(selected):
    """Зона из stand_dir: точное имя кластера или код в конце; поля EXTRA_ZONES сливаются."""
    cluster = selected.get("stand_dir", "")
    code = cluster.rsplit("-", 1)[-1] if cluster else ""
    for z in WORKSPACE_ZONES:
        if z["cluster"] == cluster or z["code"] == code:
            result = dict(z)
            for extra in EXTRA_ZONES:
                if extra["label"] == z["cluster"]:
                    result.update(extra)
                    break
            return result
    return None

# --- Управление пространствами: функции подменю ---
def _prompt_email(prompt, error_note):
    email = input(f"  {prompt}: ").strip()
    if not email or "@" not in email:
        print_warn(error_note)
        return None
    return email


def _prompt_emails(prompt, error_note):
    """Список email через запятую (один email — тоже работает)."""
    raw = input(f"  {prompt}: ").strip()
    emails = [x.strip() for x in re.split(r"[,\s]+", raw) if x.strip()]
    if not emails or any("@" not in e for e in emails):
        print_warn(error_note)
        return None
    return emails


def _prompt_dry_run():
    """Спрашивает режим запуска. True — DRY RUN, False — боевой, None — отмена."""
    choice = input(f"{action_label()} Режим запуска: {colorize('1', CYAN)} — DRY RUN, {colorize('2', CYAN)} — боевой (по умолчанию 1): ").strip().lower()
    if choice in ("", "1"):
        return True
    if choice == "2":
        return False
    print_warn("Непонятный ответ, операция отменена.")
    return None


def _run_with_dry_run(zone, script_name, generate, skip_note):
    """DRY RUN → сохранение скрипта → (опционально) боевой запуск; generate(dry_run) — текст скрипта."""
    dry_run = _prompt_dry_run()
    if dry_run is None:
        return
    open_workspace_exec(zone["code"], generate(dry_run), script_name, zone.get("argocd_base"))
    if not dry_run:
        return
    print_info("После сверки вывода DRY RUN — запустите боевой режим.")
    apply_now = input(f"{action_label()} Запустить в боевом режиме сейчас? ({colorize('yes', CYAN)}/{colorize('no', CYAN)}): ").strip().lower()
    if apply_now in ("yes", "y"):
        open_workspace_exec(zone["code"], generate(False), script_name, zone.get("argocd_base"))
    else:
        print_info(skip_note)


def ws_change_owner(selected, zone):
    ids = prompt_workspace_ids()
    if not ids:
        print_warn("Не введено ни одного ID, операция отменена.")
        return
    email = _prompt_email("Email нового владельца", "Некорректный email нового владельца, операция отменена.")
    if not email:
        return
    ygg, hm = prompt_db_urls(selected, zone)
    _run_with_dry_run(
        zone, "change_workspace_owner.py",
        lambda dry: generate_change_owner_script(ids, email, dry, ygg, hm),
        "Скрипт с DRY_RUN=True сохранён в exec/. Для боевого запуска повторите операцию.",
    )


def ws_mass_add(selected, zone):
    print_warn("Скрипт идемпотентен, но без dry run — запуск сразу применяет изменения.")
    ids = prompt_workspace_ids()
    if not ids:
        print_warn("Не введено ни одного ID, операция отменена.")
        return
    ygg, hm = prompt_db_urls(selected, zone)
    confirm = input(f"{action_label()} Запустить в боевом режиме (изменения будут применены)? ({colorize('yes', CYAN)}/{colorize('no', CYAN)}): ").strip().lower()
    if confirm not in ("yes", "y"):
        print_warn("Запуск отменён, возврат в меню.")
        return
    script = generate_mass_add_script(ids, ygg, hm)
    open_workspace_exec(zone["code"], script, "add_all_users_to_workspace.py", zone.get("argocd_base"))


def ws_add_user(selected, zone):
    ids = prompt_workspace_ids()
    if not ids:
        print_warn("Не введено ни одного ID, операция отменена.")
        return
    emails = _prompt_emails("Email пользователей (через запятую)", "Некорректный email, операция отменена.")
    if not emails:
        return
    ygg, hm = prompt_db_urls(selected, zone)
    _run_with_dry_run(
        zone, "add_user_to_workspace.py",
        lambda dry: generate_add_user_script(ids, emails, dry, ygg, hm),
        "Скрипт с DRY_RUN=True сохранён в exec/.",
    )

def ws_remove_user(selected, zone):
    ids = prompt_workspace_ids()
    if not ids:
        print_warn("Не введено ни одного ID, операция отменена.")
        return
    emails = _prompt_emails("Email пользователей (через запятую)", "Некорректный email, операция отменена.")
    if not emails:
        return
    extra_ids_raw = input(f"{action_label()} Дополнительные user_id через запятую (если пользователь не находится по email, Enter — пропустить): ").strip()
    extra_ids = [x.strip() for x in extra_ids_raw.split(",") if x.strip()] if extra_ids_raw else []
    ygg, hm = prompt_db_urls(selected, zone)
    print_warn("Вы точно хотите удалить пользователя из пространства?")
    for e in emails:
        print(colorize(f"    - {e}", YELLOW))
    confirm = input(f"{action_label()} Подтвердите (y/n): ").strip().lower()
    if confirm not in ("y", "yes"):
        print_info("Операция отменена.")
        return
    _run_with_dry_run(
        zone, "remove_user_from_workspace.py",
        lambda dry: generate_remove_user_script(ids, emails, dry, ygg, hm, extra_ids),
        "Скрипт с DRY_RUN=True сохранён в exec/.",
    )


def ws_add_base_spaces(selected, zone):
    """Базовые пространства (только стенд demo); ID и DSN — константы, нужен только email."""
    if (selected.get("cowork_subdomain") or "").lower() != DEMO_STAND_SUBDOMAIN:
        print_warn(f"Опция доступна только для стенда {DEMO_STAND_SUBDOMAIN} "
                   f"(текущий: {selected.get('cowork_subdomain') or 'не определён'}).")
        return
    emails = _prompt_emails("Email пользователей (через запятую)", "Некорректный email, операция отменена.")
    if not emails:
        return
    _run_with_dry_run(
        zone, "add_user_to_base_workspaces.py",
        lambda dry: generate_add_user_script(BASE_WORKSPACE_IDS, emails, dry, DEMO_YGGDRASIL_URL, DEMO_HEIMDALL_URL),
        "Скрипт с DRY_RUN=True сохранён в exec/.",
    )


def ws_list_workspaces(selected, zone):
    ygg, _ = prompt_db_urls(selected, zone, need_heimdall=False)
    script = generate_list_workspaces_script(ygg)
    open_workspace_exec(zone["code"], script, "list_workspaces.py", zone.get("argocd_base"))

def fix_empty_roles(selected, zone):
    """Пункт 7: пользователи без глобальной роли (heimdall_auth) → роль «Участник»."""
    hm = prompt_heimdall_url(selected, zone)
    _run_with_dry_run(
        zone, "fix_empty_roles.py",
        lambda dry: generate_fix_empty_roles_script(dry, hm),
        "Скрипт с DRY_RUN=True сохранён в exec/.",
    )

# --- Сброс зависших сессий (stale running → active) ---
_RESET_STALE_SESSIONS_TEMPLATE = '''from datetime import datetime, timezone

# True — только показать, какие сессии будут сброшены, ничего не менять
DRY_RUN = __DRY__
# Порог staleness в минутах: сессия считается зависшей, если last_active_at старше этого значения.
STALE_MINUTES = 5
# Конкретный session_id для точечного сброса (None — массовый режим по порогу).
SESSION_ID = __SESSION_ID__
DEFAULT_YGGDRASIL_URL = __YGG__

async def find_stale_sessions(connection: asyncpg.Connection) -> list[dict]:
    if SESSION_ID is not None:
        # Точечный режим: одна конкретная сессия, без проверки порога.
        rows = await connection.fetch(
            """
            SELECT id::text AS id, status, last_active_at
            FROM sessions
            WHERE id = $1 AND status = 'running'
              AND interrupt_data IS NULL
            """,
            SESSION_ID,
        )
    else:
        # Массовый режим: все stale running-сессии старше порога.
        rows = await connection.fetch(
            """
            SELECT id::text AS id, status, last_active_at
            FROM sessions
            WHERE status = 'running'
              AND interrupt_data IS NULL
              AND last_active_at < now() - make_interval(mins => $1)
            ORDER BY last_active_at
            """,
            STALE_MINUTES,
        )
    return [dict(r) for r in rows]

async def main() -> None:
    yggdrasil_url = get_database_url(("YGGDRASIL_DATABASE_URL",), DEFAULT_YGGDRASIL_URL)
    connection = await asyncpg.connect(yggdrasil_url)
    try:
        mode = f"точечный (session_id={SESSION_ID})" if SESSION_ID is not None else f"массовый (порог {STALE_MINUTES} мин)"
        print(f"режим: {'DRY RUN — ничего не будет изменено' if DRY_RUN else 'APPLY'}")
        print(f"способ поиска: {mode}")
        print()

        stale = await find_stale_sessions(connection)
        if not stale:
            print("Зависших сессий не найдено.")
            return

        print(f"Найдено зависших сессий: {len(stale)}")
        print()
        for row in stale:
            stuck_for = (datetime.now(timezone.utc) - row["last_active_at"]).total_seconds() / 60
            print(f"  session_id={row['id']}")
            print(f"    last_active_at: {row['last_active_at']} UTC")
            print(f"    застряла: ~{stuck_for:.1f} мин")
        print()

        if DRY_RUN:
            print(f"[DRY RUN] Будут сброшены: {len(stale)} шт.")
            print("[DRY RUN] Ничего не изменено. Для боевого запуска запустите скрипт с DRY_RUN=False.")
            return

        updated = 0
        for row in stale:
            res = await connection.execute(
                """
                UPDATE sessions
                SET status = 'active', last_active_at = now()
                WHERE id = $1 AND status = 'running' AND interrupt_data IS NULL
                """,
                row["id"],
            )
            if res == "UPDATE 1":
                updated += 1
                stuck_for = (datetime.now(timezone.utc) - row["last_active_at"]).total_seconds() / 60
                print(f"[OK] session_id={row['id']} сброшена (застряла ~{stuck_for:.1f} мин)")
            else:
                print(f"[SKIP] session_id={row['id']}: статус уже изменился или есть interrupt_data")
        print()
        print(f"итого: сброшено={updated}, пропущено={len(stale) - updated}")
    finally:
        await connection.close()

if __name__ == "__main__":
    asyncio.run(main())
'''


def generate_reset_stale_sessions_script(dry_run, ygg_url, session_id=None):
    sid = json.dumps(session_id) if session_id else "None"
    return _render_script(_RESET_STALE_SESSIONS_TEMPLATE,
                          DRY="True" if dry_run else "False",
                          SESSION_ID=sid,
                          YGG=json.dumps(ygg_url))


def reset_stale_sessions(selected, zone):
    """Пункт 10: сброс зависших сессий (running → active) в БД Yggdrasil."""
    print_info("Выберите режим сброса:")
    print("  1. Все зависшие сессии (stale > 5 минут)")
    print("  2. Конкретная сессия по ID")
    choice = input_with_timeout("Режим (1/2): ", INPUT_TIMEOUT)
    if choice is None:
        print_warn("Таймаут ожидания. Операция отменена.")
        return
    choice = choice.strip()
    session_id = None
    if choice == "2":
        raw = input(f"{action_label()} Session ID (UUID): ").strip()
        import uuid as _uuid
        try:
            _uuid.UUID(raw)
        except ValueError:
            print_error(f"Некорректный UUID: {raw}. Операция отменена.")
            return
        session_id = raw
    elif choice != "1":
        print_warn("Неверный ввод. Операция отменена.")
        return
    ygg, _ = prompt_db_urls(selected, zone, need_heimdall=False)
    _run_with_dry_run(
        zone, "reset_stale_sessions.py",
        lambda dry: generate_reset_stale_sessions_script(dry, ygg, session_id),
        "Скрипт с DRY_RUN=True сохранён в exec/. Для боевого запуска повторите операцию.",
    )


# --- Создание аккаунтов на стенде (auth_sdk в поде heimdall-auth) ---
_CREATE_ACCOUNTS_TEMPLATE = '''import asyncio, secrets, string, sys, httpx
try:
    from auth_sdk import AuthClient, KeycloakProviderConfig
except ModuleNotFoundError:
    from heimdall_auth import AuthClient, KeycloakProviderConfig
try:
    from auth_service.bootstrap.config import get_settings
except ModuleNotFoundError:
    from auth_service.config import get_settings

_ESC = chr(27)
GREEN, RED, YELLOW, RESET = f"{_ESC}[32m", f"{_ESC}[31m", f"{_ESC}[33m", f"{_ESC}[0m"

# Логин = часть до @; email собирается как логин@домен стенда
USERS = [
__USERS__
]
DOMAIN = __DOMAIN__

def make_password(length=24):
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*()-_=+"
    while True:
        password = "".join(secrets.choice(alphabet) for _ in range(length))
        if any(c.islower() for c in password) and any(c.isupper() for c in password) and any(c.isdigit() for c in password) and any(c in "!@#$%^&*()-_=+" for c in password):
            return password

async def main():
    if not DOMAIN or "@" in DOMAIN:
        print(f"{RED}ERROR:{RESET} заполните корректный домен")
        sys.exit(2)
    if not USERS:
        print(f"{RED}ERROR:{RESET} список пользователей пуст")
        sys.exit(2)
    settings = get_settings()
    async with httpx.AsyncClient() as http:
        auth_client = AuthClient(
            config=KeycloakProviderConfig(
                issuer_url=settings.provider_issuer_url,
                realm=settings.provider_realm,
                client_id=settings.provider_client_id,
                client_secret=settings.provider_client_secret,
            ),
            http_client=http,
        )
        created_count = 0
        reused_count = 0
        for login in USERS:
            email = f"{login}@{DOMAIN}"
            fio = login
            parts = fio.split()
            last_name = parts[0]
            first_name = " ".join(parts[1:]) if len(parts) > 1 else "Technical"
            user = await auth_client.find_user_by_email(email=email)
            if user is None:
                user = await auth_client.create_provider_user(email=email, first_name=first_name, last_name=last_name)
                created = True
            else:
                created = False
            password = make_password()
            await auth_client.reset_user_password(user_id=user.id, new_password=password)
            action = "создан новый пользователь" if created else "использован существующий пользователь"
            print(f"{GREEN}[OK]{RESET} {action}")
            print("")
            print(f"  email:    {email}")
            print(f"  password: {password}")
            print(f"  user_id:  {user.id}")
            if created:
                created_count += 1
            else:
                reused_count += 1
        print("")
        print(f"ИТОГО: создано новых: {created_count}, использовано существующих: {reused_count}")

if __name__ == "__main__":
    asyncio.run(main())
'''


def generate_create_accounts_script(logins, domain):
    """Команда heredoc для пода heimdall-auth: создание/сброс паролей нескольких аккаунтов."""
    body = (_CREATE_ACCOUNTS_TEMPLATE
            .replace("__USERS__", _json_list_block(logins))
            .replace("__DOMAIN__", json.dumps(domain)))
    return f"/app/services/auth/.venv/bin/python <<'PY'\n{body}PY\n"


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def parse_accounts_output(text):
    """Разбор вывода create_accounts: пары (email, password) в порядке появления."""
    text = _ANSI_RE.sub("", text or "")
    accounts = []
    current_email = None
    for line in text.splitlines():
        m = re.match(r"^\s*email:\s*(\S+@\S+)\s*$", line)
        if m:
            current_email = m.group(1)
            continue
        m = re.match(r"^\s*password:\s*(\S+)\s*$", line)
        if m and current_email:
            accounts.append((current_email, m.group(1)))
            current_email = None
    return accounts


def _write_accounts_xlsx(path, accounts):
    """Минимальный .xlsx (zip + XML, только stdlib): заголовки email/password + строки."""
    def esc(value):
        return (str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                .replace('"', "&quot;"))
    rows_xml = ['<row r="1"><c r="A1" t="inlineStr"><is><t>email</t></is></c>'
                '<c r="B1" t="inlineStr"><is><t>password</t></is></c></row>']
    for i, (email, password) in enumerate(accounts, start=2):
        rows_xml.append(
            f'<row r="{i}">'
            f'<c r="A{i}" t="inlineStr"><is><t>{esc(email)}</t></is></c>'
            f'<c r="B{i}" t="inlineStr"><is><t>{esc(password)}</t></is></c>'
            f'</row>'
        )
    sheet = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
             f'<sheetData>{"".join(rows_xml)}</sheetData></worksheet>')
    content_types = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                     '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                     '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                     '<Default Extension="xml" ContentType="application/xml"/>'
                     '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                     '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                     '</Types>')
    root_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                 '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                 '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
                 '</Relationships>')
    workbook = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                '<sheets><sheet name="accounts" sheetId="1" r:id="rId1"/></sheets></workbook>')
    workbook_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                     '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                     '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
                     '</Relationships>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", root_rels)
        zf.writestr("xl/workbook.xml", workbook)
        zf.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        zf.writestr("xl/worksheets/sheet1.xml", sheet)
    return path


def _accounts_domain(selected):
    """Домен для email аккаунтов: из admin_email или из cowork_subdomain."""
    admin_email = (selected.get("admin_email") or "").strip()
    if "@" in admin_email:
        return admin_email.rsplit("@", 1)[1].strip().lower()
    subdomain = (selected.get("cowork_subdomain") or "").strip().lower()
    return subdomain or None


def create_stand_accounts(selected, zone):
    """Пункт 9: создать несколько аккаунтов на стенде → Excel с email/password."""
    print_info("Введите логины через запятую (логин — часть до @; "
               "FIO будет подставлен из логина автоматически).")
    raw = input(f"{action_label()} Логины: ").strip()
    logins = []
    for token in re.split(r"[,\s]+", raw):
        token = token.strip()
        if not token:
            continue
        if "@" in token:
            token = token.split("@", 1)[0]
        logins.append(token)
    if not logins or any(not re.fullmatch(r"[A-Za-z0-9._-]+", l) for l in logins):
        print_warn("Некорректные логины (допустимы буквы, цифры, точка, дефис, underscore), операция отменена.")
        return
    if len(set(logins)) != len(logins):
        print_warn("Есть повторяющиеся логины, операция отменена.")
        return
    domain = _accounts_domain(selected)
    print_info(f"Домен для email: {domain or 'не определён'}")
    override = input(f"{action_label()} Enter — принять, либо введите свой домен: ").strip().lower()
    if override:
        domain = override
    if not domain or "@" in domain or " " in domain:
        print_warn("Некорректный домен, операция отменена.")
        return
    open_pod_exec._opened = False
    print_info("Открываю приложение heimdall в ArgoCD — скопируйте скрипт в веб-терминал.")
    open_pod_exec(zone["code"], _stand_name(selected), "heimdall", zone.get("app_template"), zone.get("argocd_base"))
    script = generate_create_accounts_script(logins, domain)
    out = EXEC_DIR / f"create_accounts_{selected.get('folder') or 'stand'}.sh"
    EXEC_DIR.mkdir(parents=True, exist_ok=True)
    out.write_text(script, encoding="utf-8")
    print_info(f"Скрипт сохранён: {out}")
    print_info("В списке приложения выберите под heimdall-auth* (вкладка exec), "
               f"скопируйте содержимое {out.name} целиком в веб-терминал и запустите.")
    paste = input_with_timeout(f"{action_label()} Вставьте вывод скрипта целиком (Enter без текста — отмена): ", INPUT_TIMEOUT)
    if paste is None:
        print_warn("Операция отменена.")
        return
    accounts = parse_accounts_output(paste)
    if not accounts:
        print_warn("Не удалось распознать ни одной пары email/password в выводе, операция отменена.")
        return
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    # Папка accounts/ рядом с finder-pro.py: создаём только если её ещё нет
    if not ACCOUNTS_DIR.exists():
        ACCOUNTS_DIR.mkdir(parents=True)
    xlsx_path = ACCOUNTS_DIR / f"accounts_{selected.get('folder') or 'stand'}_{stamp}.xlsx"
    try:
        _write_accounts_xlsx(xlsx_path, accounts)
    except OSError as error:
        print_error(f"Не удалось сохранить Excel: {error}")
        return
    print_info(f"Готово: {len(accounts)} аккаунтов → {xlsx_path}")

# --- Токены LLM (Langfuse) ---
def _langfuse_ch_query(stand_name, days):
    """SQL для прямого подсчёта токенов в ClickHouse Langfuse (разбивка по моделям + input/output)."""
    return (
        "SELECT provided_model_name AS model, count() AS calls, "
        "sum(usage_details['input']) AS input_tokens, sum(usage_details['output']) AS output_tokens, "
        "sum(usage_details['total']) AS total_tokens "
        "FROM default.observations "
        f"WHERE environment = '{stand_name}' AND type = 'GENERATION' "
        "AND start_time >= now() - INTERVAL {days} DAY "
        "GROUP BY model ORDER BY total_tokens DESC FORMAT TabSeparated"
    ).format(days=days)


def _langfuse_ch_direct_command(stand_name, days):
    """Команда clickhouse-client для выполнения прямо в поде (веб-терминал ArgoCD), без kubectl."""
    sql = _langfuse_ch_query(stand_name, days)
    dq = chr(34)  # двойная кавычка
    return f"clickhouse-client --query {dq}{sql}{dq}"


def _parse_ch_output(raw):
    """Разбор вывода clickhouse-client; None при пустом/некорректном вводе.

    Понимает и TabSeparated, и пробельный формат (табы из веб-терминала ArgoCD превращаются в пробелы):
    последние 4 поля строки — числа (calls, input, output, total), всё остальное — имя модели.
    Возвращает (rows, totals): rows — [(model, calls, inp, out, total)], totals — (calls, inp, out, total)."""
    def _num(s):
        s = s.strip()
        if s in ("", r"\N", "NULL"):
            return 0
        return int(float(s))
    def _split_line(line):
        parts = line.split("\t")
        if len(parts) >= 5:
            return parts[0], parts[1:5]
        toks = line.split()
        if len(toks) < 5:
            return None
        return " ".join(toks[:-4]), toks[-4:]
    parsed = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        split = _split_line(line)
        if split is None:
            continue
        model, nums = split
        try:
            calls = _num(nums[0])
            inp = _num(nums[1])
            outp = _num(nums[2])
            tot = _num(nums[3])
        except ValueError:
            continue
        if tot == 0:
            tot = inp + outp
        parsed.append((model or "(не указано)", calls, inp, outp, tot))
    if not parsed:
        return None
    totals = (
        sum(r[1] for r in parsed),
        sum(r[2] for r in parsed),
        sum(r[3] for r in parsed),
        sum(r[4] for r in parsed),
    )
    return parsed, totals



def _fmt_int(value):
    """Число с разделителем тысяч по-русски (пробел вместо запятой)."""
    return f"{value:,}".replace(",", " ")



def langfuse_tokens_clickhouse(selected, zone, days):
    """Пункт 8, источник ClickHouse: команда → пользователь выполняет в терминале → вставляет результат."""
    stand_name = selected.get("stand_name") or selected.get("folder", "")
    # ClickHouse один на все зоны — ссылка всегда tb01
    ch_app_url = ("https://argocd-tb01.cowork.ru/applications/langfuse/langfuse"
                  "?view=tree&node=%2FPod%2Flangfuse%2Fchi-langfuse-default-0-0-0&tab=exec&resource=")
    print_divider()
    print_info(f"Прямой подсчёт через ClickHouse (стенд {stand_name}, последние {days} дн.).")
    open_url(ch_app_url)
    print_info(f"Открыт под chi-langfuse-default-0-0-0 (приложение langfuse, вкладка exec).")
    print("Выполните команду в открытом веб-терминале пода:")
    print()
    print(colorize(_langfuse_ch_direct_command(stand_name, days), CYAN))
    print()
    raw = input_with_timeout(f"{action_label()} Вставьте результат целиком (Ctrl+C — отмена): ", INPUT_TIMEOUT)
    if raw is None:
        print_warn("Таймаут ожидания. Возврат к меню.")
        return
    # пустой результат (команда + промпт, без данных) — нормальная ситуация
    stripped = raw.strip()
    if not stripped or all(
        l.strip().startswith(("root@", "#", "$")) or "clickhouse-client" in l or not l.strip()
        for l in stripped.splitlines()
    ):
        subdomain = selected.get("cowork_subdomain") or ""
        stand_label = f"{stand_name} ({subdomain})" if subdomain else stand_name
        print_divider()
        print_info(f"Данных нет: за последние {days} дн. на стенде {stand_label} не найдено вызовов LLM.")
        return
    parsed = _parse_ch_output(raw)
    if parsed is None:
        print_error("Не удалось разобрать результат: ожидается строка «модель вызовы input output total» (табы или пробелы).")
        return
    _, totals = parsed
    calls, inp, outp, total = totals
    print_divider()
    print_info(f"Токены LLM за последние {days} дн.")
    print(f"Вызовов LLM: {_fmt_int(calls)}")
    print(f"Потрачено за период: {_fmt_int(total)} токенов")
    print(f"  запрос к модели (input):   {_fmt_int(inp)}")
    print(f"  ответ модели (output):     {_fmt_int(outp)}")


def langfuse_tokens(selected, zone):
    """Пункт 8: сводка по токенам LLM из ClickHouse напрямую (только чтение)."""
    print()
    print("  Период:")
    print("   1. Последние 7 дней")
    print("   2. Последние 30 дней")
    print("   3. Другой период (дней)")
    choice = input_with_timeout("Выберите период (1-3): ", INPUT_TIMEOUT)
    if choice is None:
        print_warn("Таймаут ожидания. Возврат к меню.")
        return
    choice = choice.strip()
    if choice == "1":
        days = 7
    elif choice == "2":
        days = 30
    elif choice.isdigit() and 1 <= int(choice) <= 365:
        days = int(choice)
    else:
        print_warn("Неверный период, операция отменена.")
        return
    langfuse_tokens_clickhouse(selected, zone, days)



# --- Меню ---
_WS_ACTIONS = {
    "1": ws_change_owner,
    "2": ws_add_user,
    "3": ws_mass_add,
    "4": ws_list_workspaces,
    "5": ws_remove_user,
    "6": ws_add_base_spaces,
}


def workspace_menu(selected):
    is_demo = (selected.get("cowork_subdomain") or "").lower() == DEMO_STAND_SUBDOMAIN  # пункт 6 — только demo
    actions = dict(_WS_ACTIONS)
    if not is_demo:
        actions.pop("6")
    while True:
        print_action("Управление пространствами")
        print("  1. Сменить владельца пространства")
        print("  2. Добавить пользователя в пространство")
        print("  3. Массовое добавление в пространство")
        print("  4. Показать все пространства на стенде")
        print(colorize("  5. Удалить пользователя из пространства", RED))
        if is_demo:
            print("  6. Добавить базовые пространства")
        print("  0. Назад")
        choice = input_with_timeout("Выберите функцию: ", INPUT_TIMEOUT)
        if choice is None:
            break
        choice = choice.strip()
        if choice == "0":
            break
        handler = actions.get(choice)
        if handler is None:
            print_warn("Неверный ввод.")
            continue
        zone = resolve_zone(selected)
        if zone:
            handler(selected, zone)
        else:
            print_warn(f"Не удалось определить зону из stand_dir={selected.get('stand_dir', '?')}.")


def stand_action_menu(selected, script_dir):
    while True:
        print_action("Выбрать действие")
        print("  1. Открыть Admin Helper и ArgoCD")
        print("  2. Зайти на тенант")
        print("  3. Открыть консоль тенантов")
        print("  4. Запустить FAQ-агента")
        print("  5. Открыть конфигурацию стенда")
        print("  6. Управление пространствами")
        print("  7. Починить роли на стенде")
        print("  8. Токены LLM (ClickHouse)")
        print("  9. Создать аккаунты (Excel с email/password)")
        print("  10. Сброс зависших сессий")
        print("  0. Вернуться к поиску")
        print()
        action = input_with_timeout("Выберите действие (0-10): ", INPUT_TIMEOUT)
        if action is None:
            print_warn("Таймаут ожидания. Возврат к поиску.")
            break
        action = action.strip()
        if action == "0":
            break
        elif action == "1":
            open_links(selected, script_dir, resolve_zone(selected))
        elif action == "2":
            open_tenant(selected)
        elif action == "3":
            open_tenant_console()
        elif action == "4":
            open_faq_agent()
        elif action == "5":
            open_stand_config(selected)
        elif action == "6":
            workspace_menu(selected)
        elif action == "7":
            zone = resolve_zone(selected)
            if zone:
                fix_empty_roles(selected, zone)
            else:
                print_warn(f"Не удалось определить зону из stand_dir={selected.get('stand_dir', '?')}.")
        elif action == "8":
            zone = resolve_zone(selected)
            if zone:
                langfuse_tokens(selected, zone)
            else:
                print_warn(f"Не удалось определить зону из stand_dir={selected.get('stand_dir', '?')}.")
        elif action == "9":
            zone = resolve_zone(selected)
            if zone:
                create_stand_accounts(selected, zone)
            else:
                print_warn(f"Не удалось определить зону из stand_dir={selected.get('stand_dir', '?')}.")
        elif action == "10":
            zone = resolve_zone(selected)
            if zone:
                reset_stale_sessions(selected, zone)
            else:
                print_warn(f"Не удалось определить зону из stand_dir={selected.get('stand_dir', '?')}.")
        else:
            print_warn("Неверный ввод, попробуйте снова.")

# --- Точка входа ---



def _prompt_search_criteria():
    print("\n" + "=" * 50)
    print("Введите критерии поиска (заполни хотя бы одно поле):")
    subdomain = input("  cowork_subdomain: ").strip()
    email = input("  email: ").strip()
    inn = input("  ИНН: ").strip()
    stand_name = input("  stand_name: ").strip()
    if not any((subdomain, email, inn, stand_name)):
        print_warn("Ошибка: необходимо заполнить хотя бы одно поле.")
        return None
    return {
        "subdomain": subdomain or None,
        "email": email or None,
        "inn": inn or None,
        "stand_name": stand_name or None,
    }


def _handle_keyboard_interrupt():
    print("\nВыполнение остановлено пользователем.")
    raise SystemExit(130)
def _init_environment(script_dir):
    """Общая инициализация (репозиторий, зоны, pull, watcher) для обоих режимов запуска."""
    global WATCHER
    try:
        repo_root = get_repo_root(script_dir)
    except FileNotFoundError as e:
        print_error(str(e))
        print_info("Попробуем найти репозиторий вручную.")
        repo_root = _prompt_repo_root(script_dir)
    while not _repo_has_content(repo_root):
        print_error(f"Папка {repo_root} не содержит данных репозитория "
                    f"(ожидаются папки: {', '.join(ALLOWED_REPO_DIRS)}).")
        print_info("Возможные причины: содержимое папки перенесли в другое место, "
                   "клонирование прервалось или это не та папка.")
        print_action("Что делать?")
        print("  1. Указать другой путь к репозиторию")
        print("  2. Склонировать репозиторий в эту папку")
        print("  3. Выйти")
        choice = input_with_timeout("Выберите вариант (1-3): ", INPUT_TIMEOUT)
        if choice is None or choice.strip() == "3":
            print_warn("Завершение работы.")
            sys.exit(1)
        if choice.strip() == "1":
            repo_root = _prompt_repo_root(script_dir)
            continue
        if choice.strip() == "2":
            if not _clone_repo(repo_root):
                print_info("Если папка не пустая — очистите её вручную и повторите попытку.")
                continue
            _save_repo_path(repo_root, quiet=True)
            print_info(f"Репозиторий склонирован в {repo_root}, путь сохранён.")
            break
        print_warn("Неверный ввод, попробуйте снова.")

    _auto_restore_repo(repo_root)
    pull_result = pull_repo(repo_root)
    zones = ZONES.init(repo_root)
    ZONES.verify(repo_root, zones)
    report_pull(repo_root, script_dir / "news", *pull_result)
    WATCHER = RepoChangeWatcher(repo_root)
    WATCHER.start()
    return repo_root, zones
def main():
    print_logo()
    select_runtime_mode()
    script_dir = Path(__file__).parent.resolve()
    # Режим запуска: без аргумента — сразу поиск стендов (старый вид),
    # «token» — сразу подсчёт утилизации токенов.
    mode = (sys.argv[1].strip().lower() if len(sys.argv) > 1 else "")
    repo_root, zones = _init_environment(script_dir)
    if mode == "token":
        token_utilization_report(repo_root, script_dir, zones)
        return
    while True:
        # Фоновая проверка (раз в 15 минут): уведомляем только в простое,
        # перед экраном критериев поиска — во время работы в меню не мешаем.
        check_and_notify(repo_root, script_dir / "news", zones)
        criteria = _prompt_search_criteria()
        if criteria is None:
            continue
        results = find_stands(repo_root, zones, criteria)
        if not results:
            print_warn("Не найдено стендов, соответствующих критериям.")
            continue
        print_info(f"Найдено {len(results)} стендов:")
        for idx, data in enumerate(results, 1):
            _print_stand(idx, data)
        selected = select_stand(results)
        if selected is None:
            continue
        stand_action_menu(selected, script_dir)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        _handle_keyboard_interrupt()
