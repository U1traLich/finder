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
import webbrowser
from datetime import datetime
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
VERSION = "v2.7.15"

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
    spinner = itertools.cycle(['\\', '|', '/', '-'])
    while not stop_event.is_set():
        sys.stdout.write(next(spinner) + '\b')
        sys.stdout.flush()
        time.sleep(0.1)

# --- Ввод с таймаутом ---
def input_with_timeout(prompt, timeout_seconds):
    """Ввод строки с таймаутом; None при истечении."""
    print(prompt, end='', flush=True)
    if sys.platform == "win32":
        import msvcrt
        chars = []
        start = time.time()
        while True:
            if time.time() - start > timeout_seconds:
                print()
                return None
            if msvcrt.kbhit():
                ch = msvcrt.getwche()
                if ch in ('\r', '\n'):
                    print()
                    break
                elif ch == '\b':
                    if chars:
                        chars.pop()
                        sys.stdout.write('\b \b')
                        sys.stdout.flush()
                else:
                    chars.append(ch)
            time.sleep(0.05)
        return ''.join(chars).strip()
    else:
        import select
        ready, _, _ = select.select([sys.stdin], [], [], timeout_seconds)
        if ready:
            line = sys.stdin.readline()
            if line:
                return line.strip()
            else:
                return None
        else:
            print()
            return None

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
def load_zones():
    if not ZONES_CONFIG_FILE.exists():
        return []
    try:
        zones = json.loads(ZONES_CONFIG_FILE.read_text(encoding="utf-8"))
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
        save_zones(zones)
    return zones


def save_zones(zones):
    ZONES_DIR.mkdir(parents=True, exist_ok=True)
    ZONES_CONFIG_FILE.write_text(json.dumps(zones, ensure_ascii=False, indent=2), encoding="utf-8")


def make_zone_from_cluster(cluster_name):
    # cluster_name: "cce-prod-st02"
    code = cluster_name.rsplit("-", 1)[-1]  # st02, tb01, tb04
    return {
        "path": f"cce-prod/cowork/clusters/{cluster_name}",
        "label": cluster_name,
        "url": f"https://argocd-{code}.cowork.ru/applications/cowork/{{folder}}?resource="
    }


def get_remote_clusters(repo_root):
    clusters_path = repo_root / "cce-prod/cowork/clusters"
    if not clusters_path.exists():
        return []
    return sorted(d.name for d in clusters_path.iterdir() if d.is_dir())


def discover_and_save_zones(repo_root):
    zones = [make_zone_from_cluster(c) for c in get_remote_clusters(repo_root)]
    zones += [dict(z) for z in EXTRA_ZONES if _zone_path_allowed(z["path"])]
    save_zones(zones)
    return zones


def check_and_update_zones(repo_root, zones):
    remote_clusters = get_remote_clusters(repo_root)
    # Устаревшие зоны (больше не актуальны): убираем из конфига до проверки доступности.
    allowed_paths = {f"cce-prod/cowork/clusters/{c}" for c in remote_clusters}
    allowed_paths |= {z["path"] for z in EXTRA_ZONES}
    stale = [z for z in zones if z["path"] not in allowed_paths]
    if stale:
        zones[:] = [z for z in zones if z["path"] in allowed_paths]
        save_zones(zones)
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
            save_zones(zones)
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
                    save_zones(zones)
                    print_info(f"Обновлена зона: {extra['label']} ({extra['path']}).")
                break

    updated = False
    for cluster in (c for c in remote_clusters if c not in known):
        print_warn(f"Обнаружен новый кластер: {cluster}")
        answer = input_with_timeout("Добавить в конфигурацию Finder новый кластер? (yes/no): ", 60)
        if answer and answer.lower() in ("y", "yes"):
            zones.append(make_zone_from_cluster(cluster))
            save_zones(zones)
            print_info(f"Кластер {cluster} добавлен в конфигурацию.")
            updated = True
        else:
            print_info(f"Кластер {cluster} пропущен.")
    if updated:
        print_info("Конфигурация обновлена.")

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
        answer = input_with_timeout("Восстановить их из последнего коммита? (yes/no): ", 60)
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
            f"(Enter — склонировать в {REPO_PATH}): ", 300
        )
        if raw is None:
            print_error("Таймаут ожидания. Запустите finder повторно.")
            sys.exit(1)
        target = Path(raw.strip()).expanduser() if raw.strip() else REPO_PATH
        if (target / ".git").exists():
            _save_repo_path(target)
            return target.resolve()
        answer = input_with_timeout(
            f"[WARN] В {target} нет .git. Склонировать туда {REPO_URL}? (y/n): ", 300
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


def pull_repo(repo_root, news_dir):
    """git fetch + pull; возвращает (remote_url, head_before, head_after)."""
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

# --- Поиск стендов ---
_CONFIG_KEYS = ("stand_name", "cowork_subdomain", "admin_email", "expiry_date", "cowork_revision", "public_hostname")


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
            if not _matches(data, criteria):
                continue
            data["folder"] = stand_dir.name
            data["stand_dir"] = zone["label"]
            data["zone_path"] = zone["path"]
            data["url_template"] = zone["url"]
            data["config_path"] = config_path
            results.append(data)
    return results


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
    print(f"   cowork_revision: {data.get('cowork_revision', 'N/A')}")
    if data.get("broken"):
        print("   [broken stand]")


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
    helper = script_dir / "admin-helper-new.html"
    if helper.exists():
        open_file(str(helper))
    else:
        print_warn("admin-helper-new.html не найден.")
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

_CHANGE_OWNER_TEMPLATE = '''import asyncio
import os
import sys

import asyncpg

OWNER_ROLE_ID = "workspace.owner"
MEMBER_ROLE_ID = "workspace.member"
WORKSPACE_NAMESPACE = "workspace"

WORKSPACE_IDS = [
__IDS__
]
NEW_OWNER_EMAIL = __EMAIL__
# True — только показать, что будет сделано, ничего не менять
DRY_RUN = __DRY__

DEFAULT_YGGDRASIL_URL = __YGG__
DEFAULT_HEIMDALL_URL = __HM__


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


async def user_exists(connection: asyncpg.Connection, user_id: str) -> bool:
    return await connection.fetchval(
        "SELECT EXISTS(SELECT 1 FROM users WHERE id = $1)", user_id
    )


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

_MASS_ADD_TEMPLATE = '''import asyncio
import os

import asyncpg

MEMBER_ROLE_ID = "workspace.member"
WORKSPACE_NAMESPACE = "workspace"
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

_ADD_USER_TEMPLATE = '''import asyncio
import os
import sys
from datetime import datetime, timezone
import asyncpg

MEMBER_ROLE_ID = "workspace.member"
WORKSPACE_NAMESPACE = "workspace"

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

_REMOVE_USER_TEMPLATE = '''import asyncio
import os
import sys
import asyncpg
WORKSPACE_NAMESPACE = "workspace"
WORKSPACE_IDS = [
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
def get_database_url(variable_names: tuple[str, ...], default: str) -> str:
    for name in variable_names:
        value = os.environ.get(name)
        if value:
            return value
    return default
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

_LIST_WORKSPACES_TEMPLATE = '''import asyncio
import os
import sys

import asyncpg


def get_database_url(variable_names: tuple[str, ...], default: str) -> str:
    for name in variable_names:
        value = os.environ.get(name)
        if value:
            return value
    return default


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
    return ",\n".join(f'    "{w}"' for w in workspace_ids)


def generate_change_owner_script(workspace_ids, new_owner_email, dry_run, ygg_url, hm_url):
    return (_CHANGE_OWNER_TEMPLATE
            .replace("__IDS__", _ids_block(workspace_ids))
            .replace("__EMAIL__", json.dumps(new_owner_email))
            .replace("__DRY__", "True" if dry_run else "False")
            .replace("__YGG__", json.dumps(ygg_url))
            .replace("__HM__", json.dumps(hm_url)))


def generate_mass_add_script(workspace_ids, ygg_url, hm_url):
    return (_MASS_ADD_TEMPLATE
            .replace("__IDS__", _ids_block(workspace_ids))
            .replace("__YGG__", json.dumps(ygg_url))
            .replace("__HM__", json.dumps(hm_url)))


def generate_add_user_script(workspace_ids, user_emails, dry_run, ygg_url, hm_url):
    emails_block = ",\n".join(f'    {json.dumps(e)}' for e in user_emails)
    return (_ADD_USER_TEMPLATE
            .replace("__IDS__", _ids_block(workspace_ids))
            .replace("__EMAILS__", emails_block)
            .replace("__DRY__", "True" if dry_run else "False")
            .replace("__YGG__", json.dumps(ygg_url))
            .replace("__HM__", json.dumps(hm_url)))


def generate_remove_user_script(workspace_ids, user_emails, dry_run, ygg_url, hm_url, extra_user_ids=None):
    emails_block = ",\n".join(f'    {json.dumps(e)}' for e in user_emails)
    extra_ids = extra_user_ids or []
    extra_block = ",\n".join(f'    {json.dumps(uid)}' for uid in extra_ids) if extra_ids else "    # (пусто — не нужно)"
    return (_REMOVE_USER_TEMPLATE
            .replace("__EXTRA_IDS__", extra_block)
            .replace("__IDS__", _ids_block(workspace_ids))
            .replace("__EMAILS__", emails_block)
            .replace("__DRY__", "True" if dry_run else "False")
            .replace("__YGG__", json.dumps(ygg_url))
            .replace("__HM__", json.dumps(hm_url)))


def generate_list_workspaces_script(ygg_url):
    return _LIST_WORKSPACES_TEMPLATE.replace("__YGG__", json.dumps(ygg_url))


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


def _load_dsn_cache():
    try:
        with open(DSN_CACHE_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_dsn_cache(cache):
    try:
        DSN_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(DSN_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except OSError as error:
        print_warn(f"Не удалось сохранить кэш DSN: {error}")


def prompt_db_urls(selected, zone, need_heimdall=True):
    folder = selected.get("folder", "")
    stand_name = selected.get("stand_name") or folder

    cache = _load_dsn_cache()
    cached = cache.get(folder) or {}
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
        entry = {"yggdrasil": ygg, "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M")}
        if need_heimdall:
            entry["heimdall"] = hm
        cache[folder] = entry
        _save_dsn_cache(cache)
        print_info(f"DSN сохранены в кэш ({DSN_CACHE_PATH}) — в следующий раз вводить не придётся.")

    return ygg, hm


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
        choice = input_with_timeout("Выберите функцию: ", 60)
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
        print("  0. Вернуться к поиску")
        print()
        action = input_with_timeout("Выберите действие (0-6): ", 300)
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
        else:
            print_warn("Неверный ввод, попробуйте снова.")

# --- Точка входа ---
def _init_zones(repo_root):
    if not ZONES_CONFIG_FILE.exists():
        zones = discover_and_save_zones(repo_root)
        print_info(f"Создан файл zones_config.json с {len(zones)} кластерами.")
    else:
        zones = load_zones()
        check_and_update_zones(repo_root, zones)
    return zones


def _verify_zones(repo_root, zones):
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


def _prompt_search_criteria():
    print("\n" + "=" * 50)
    print("Введите критерии поиска (можно оставить пустым, но хотя бы одно поле):")
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
def main():
    print_logo()
    select_runtime_mode()
    script_dir = Path(__file__).parent.resolve()
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
        choice = input_with_timeout("Выберите вариант (1-3): ", 300)
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
    pull_result = pull_repo(repo_root, script_dir / "news")
    zones = _init_zones(repo_root)
    _verify_zones(repo_root, zones)
    report_pull(repo_root, script_dir / "news", *pull_result)

    while True:
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
