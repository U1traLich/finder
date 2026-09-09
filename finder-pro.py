#!/usr/bin/env python3
import subprocess, re, sys, webbrowser
import threading, itertools, time
import json
from pathlib import Path
from datetime import datetime

# --- Цветовые коды (из L1) ---
WHITE = '\033[97m'
GREEN = '\033[92m'
YELLOW = '\033[93m'
RED = '\033[91m'
CYAN = '\033[96m'
RESET = '\033[0m'

# ----- НАСТРОЙКИ L2 -----
REPO_PATH = Path.home() / "Documents/Giga/cowork/argocd"
FAQ_AGENT_URL = "https://cowork.gigab2b.ru/workspaces/c63c8b9f-bdf0-4805-a314-b8da3eaeaea7"

CONFIG = "config.yaml"
ZONES_CONFIG_FILE = Path(__file__).parent / "zones_config.json"

# --- Функции цветного вывода (с разделителем для всех кроме INFO) ---
def print_divider():
    print("\n" + "=" * 50)

def print_info(message):
    if sys.stdout.isatty():
        print(f"{WHITE}[{GREEN}INFO{WHITE}]{RESET} {message}")
    else:
        print(f"[INFO] {message}")

def print_warn(message):
    print_divider()
    if sys.stdout.isatty():
        print(f"{WHITE}[{YELLOW}WARN{WHITE}]{RESET} {message}")
    else:
        print(f"[WARN] {message}")

def print_error(message):
    print_divider()
    if sys.stdout.isatty():
        print(f"{WHITE}[{RED}ERROR{WHITE}]{RESET} {message}")
    else:
        print(f"[ERROR] {message}")

def print_action(message, end="\n"):
    print_divider()
    if sys.stdout.isatty():
        print(f"{WHITE}[{CYAN}ACTION{WHITE}]{RESET} {message}", end=end, flush=True)
    else:
        print(f"[ACTION] {message}", end=end, flush=True)

def colorize_yellow(text):
    return f"{YELLOW}{text}{RESET}" if sys.stdout.isatty() else text

# --- Анимация спиннера ---
def animate_spinner(stop_event):
    spinner = itertools.cycle(['\\', '|', '/', '-'])
    while not stop_event.is_set():
        sys.stdout.write(next(spinner) + '\b')
        sys.stdout.flush()
        time.sleep(0.1)

# --- Ввод с таймаутом ---
def input_with_timeout(prompt, timeout_seconds):
    """Ожидает ввод строки с таймаутом. Возвращает строку или None при истечении времени."""
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
def print_logo():
    logo_lines = [
        "  ██████╗ ██╗ ██████╗  █████╗ ███████╗██╗███╗   ██╗██████╗ ███████╗██████╗ ",
        "██╔════╝ ██║██╔════╝ ██╔══██╗██╔════╝██║████╗  ██║██╔══██╗██╔════╝██╔══██╗",
        "██║  ███╗██║██║  ███╗███████║█████╗  ██║██╔██╗ ██║██║  ██║█████╗  ██████╔╝",
        "██║   ██║██║██║   ██║██╔══██║██╔══╝  ██║██║╚██╗██║██║  ██║██╔══╝  ██╔══██╗",
        "╚██████╔╝██║╚██████╔╝██║  ██║██║     ██║██║ ╚████║██████╔╝███████╗██║  ██║",
        " ╚═════╝ ╚═╝ ╚═════╝ ╚═╝  ╚═╝╚═╝     ╚═╝╚═╝  ╚═══╝╚═════╝ ╚══════╝╚═╝  ╚═╝",
    ]
    max_len = max(len(line) for line in logo_lines)
    border = "=" * (max_len + 4)
    print(border)
    for line in logo_lines:
        padded = line + " " * (max_len - len(line))
        print(f"|| {padded}||")
    print(border)

# --- Утилиты для работы с зонами ---
def load_zones():
    if ZONES_CONFIG_FILE.exists():
        try:
            with open(ZONES_CONFIG_FILE, "r", encoding="utf-8") as f:
                zones = json.load(f)
                if isinstance(zones, list):
                    # Автокоррекция URL, если ранее был создан файл с багом
                    corrected = False
                    for zone in zones:
                        if "url" in zone and "argocd-cce-prod-" in zone["url"]:
                            cluster_name = zone["path"].split("/")[-1]
                            code = cluster_name.rsplit("-", 1)[-1]
                            zone["url"] = f"https://argocd-{code}.cowork.ru/applications/cowork/{{folder}}?resource="
                            corrected = True
                    if corrected:
                        save_zones(zones)
                    return zones
        except Exception:
            pass
    return []

def save_zones(zones):
    with open(ZONES_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(zones, f, ensure_ascii=False, indent=2)

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
    return sorted([d.name for d in clusters_path.iterdir() if d.is_dir()])

def discover_and_save_zones(repo_root):
    remote_clusters = get_remote_clusters(repo_root)
    zones = [make_zone_from_cluster(c) for c in remote_clusters]
    save_zones(zones)
    return zones

def check_and_update_zones(repo_root, zones):
    remote_clusters = get_remote_clusters(repo_root)
    updated = False

    for zone in zones:
        cluster_name = zone["path"].split("/")[-1]
        if cluster_name not in remote_clusters:
            print_error(f"Кластер {zone['label']} не доступен.")

    new_clusters = [c for c in remote_clusters if c not in [z["path"].split("/")[-1] for z in zones]]
    for cluster in new_clusters:
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

# --- Утилиты L2 ---
def get_repo_root(start):
    if REPO_PATH.exists() and (REPO_PATH / ".git").exists():
        return REPO_PATH.resolve()
    for p in [start, start.parent, start.parent / "argocd"]:
        if (p / ".git").exists():
            return p.resolve()
    cur = start.resolve()
    while cur != cur.parent:
        if (cur / ".git").exists():
            return cur
        cur = cur.parent
    raise FileNotFoundError("Не найден .git")

def pull_repo(repo_root, news_dir):
    stop_event = threading.Event()
    spinner_thread = threading.Thread(target=animate_spinner, args=(stop_event,), daemon=True)
    spinner_thread.start()

    if sys.stdout.isatty():
        print(f"{WHITE}[{GREEN}INFO{WHITE}]{RESET} Обновление репозитория ", end='', flush=True)
    else:
        print("[INFO] Обновление репозитория...", end='', flush=True)

    try:
        remote_url = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=repo_root,
            capture_output=True,
            text=True
        ).stdout.strip()

        head_before = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True
        ).stdout.strip()

        fetch_result = subprocess.run(
            ["git", "fetch", "origin"],
            cwd=repo_root,
            capture_output=True,
            text=True
        )
        if fetch_result.returncode != 0:
            raise RuntimeError(f"git fetch failed: {fetch_result.stderr}")

        current_branch = subprocess.run(
            ["git", "symbolic-ref", "--short", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True
        ).stdout.strip()
        if not current_branch:
            raise RuntimeError("Не удалось определить текущую ветку.")

        pull_result = subprocess.run(
            ["git", "pull", "origin", current_branch],
            cwd=repo_root,
            capture_output=True,
            text=True
        )
        if pull_result.returncode != 0:
            raise RuntimeError(f"git pull failed: {pull_result.stderr}")

        head_after = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True
        ).stdout.strip()

        stop_event.set()
        spinner_thread.join()
        if sys.stdout.isatty():
            print()

        if remote_url:
            print_info(f"Удалённый репозиторий: {remote_url}")

        if head_before and head_after and head_before != head_after:
            diff_stat = subprocess.run(
                ["git", "diff", "--stat", head_before, head_after],
                cwd=repo_root,
                capture_output=True,
                text=True
            ).stdout
            diff_full = subprocess.run(
                ["git", "diff", head_before, head_after],
                cwd=repo_root,
                capture_output=True,
                text=True
            ).stdout
            if diff_full:
                news_dir.mkdir(parents=True, exist_ok=True)
                fname = f"changes_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
                (news_dir / fname).write_text(f"Изменения от {datetime.now()}\n{'='*40}\n{diff_full}", encoding='utf-8')
                print_info(f"Полные изменения сохранены в {news_dir / fname}")
            if diff_stat:
                files = subprocess.run(
                    ["git", "diff", "--name-only", head_before, head_after],
                    cwd=repo_root,
                    capture_output=True,
                    text=True
                ).stdout.strip().split('\n')
                print_info("Изменённые файлы (cowork_subdomain):")
                for f in files:
                    if not f:
                        continue
                    try:
                        full_path = repo_root / f
                        if full_path.exists():
                            content = full_path.read_text(encoding='utf-8')
                            m = re.search(r'cowork_subdomain:\s*"?([^"\n]+)"?', content)
                            sub = m.group(1).strip() if m else "?"
                        else:
                            sub = "?"
                    except:
                        sub = "?"
                    print(f"  {f} ({colorize_yellow(sub)})")
            else:
                print_warn("Изменения затянуты, но статистика пуста.")
        else:
            print_info("Новых изменений нет.")

    except Exception as e:
        stop_event.set()
        spinner_thread.join()
        if sys.stdout.isatty():
            print()
        print_error(str(e))
        sys.exit(1)

def read_config(file_path):
    try:
        content = file_path.read_text(encoding='utf-8')
    except:
        return {}
    data = {}
    for key in ["stand_name", "cowork_subdomain", "admin_email", "expiry_date", "cowork_revision"]:
        m = re.search(rf'{key}:\s*"?([^"\n]+)"?', content)
        data[key] = m.group(1).strip() if m else None
    m = re.search(r'(?:meta\.)?inn:\s*"?([^"\n]+)"?', content)
    data['inn'] = m.group(1).strip() if m else None
    data['broken'] = '# broken stand' in content
    return data

def find_stands(root, criteria):
    results = []
    for zone in zones_global:
        base = root / zone["path"]
        if not base.exists():
            continue
        for config_path in base.rglob(CONFIG):
            stand_dir = config_path.parent
            if not stand_dir.name.startswith("cowork-"):
                continue
            data = read_config(config_path)
            match = False
            if criteria.get('subdomain') and data.get('cowork_subdomain') and criteria['subdomain'].lower() in data['cowork_subdomain'].lower():
                match = True
            if criteria.get('email') and data.get('admin_email') and criteria['email'].lower() in data['admin_email'].lower():
                match = True
            if criteria.get('inn') and data.get('inn') and criteria['inn'] in data['inn']:
                match = True
            if criteria.get('stand_name') and data.get('stand_name') and criteria['stand_name'].lower() in data['stand_name'].lower():
                match = True
            if match:
                data['folder'] = stand_dir.name
                data['stand_dir'] = zone["label"]
                data['url_template'] = zone["url"]
                data['config_path'] = config_path
                results.append(data)
    return results

def select_stand(results):
    if len(results) == 1:
        return results[0]
    print_warn("Найдено несколько стендов.")
    for i, data in enumerate(results, 1):
        sub = data.get('cowork_subdomain', 'N/A')
        print(f"  {i}. {data['folder']} ({colorize_yellow(sub)})")
    while True:
        print_action("Введите номер стенда (0 для отмены):", end="")
        choice = input().strip()
        if choice == '0':
            return None
        if choice.isdigit() and 1 <= int(choice) <= len(results):
            return results[int(choice)-1]
        print_warn("Неверный ввод, попробуйте снова.")

def open_links(selected, script_dir):
    helper = script_dir / "admin-helper-new.html"
    if helper.exists():
        webbrowser.open(str(helper))
    else:
        print_warn("admin-helper-new.html не найден.")
    url = selected['url_template'].format(folder=selected['folder'])
    webbrowser.open(url)

def open_tenant(selected):
    subdomain = selected.get('cowork_subdomain')
    if not subdomain:
        print_warn("Не указан cowork_subdomain, невозможно открыть тенант.")
        return
    url = f"https://{subdomain}.cowork.ru"
    webbrowser.open(url)

def open_tenant_console():
    url = "https://tenant-console.cowork.ru/"
    webbrowser.open(url)

def open_faq_agent():
    webbrowser.open(FAQ_AGENT_URL)

def open_stand_config(selected):
    base_url = "https://git.sberdevices.ru/gigabb/infra/cowork/argocd/-/tree/master/cce-prod/cowork/clusters"
    cluster = selected.get('stand_dir')
    folder = selected.get('folder')
    if not cluster or not folder:
        print_warn("Не удалось определить путь к конфигурации стенда.")
        return
    url = f"{base_url}/{cluster}/{folder}?ref_type=heads"
    webbrowser.open(url)

def main():
    global zones_global
    print_logo()
    script_dir = Path(__file__).parent.resolve()
    try:
        repo_root = get_repo_root(script_dir)
    except FileNotFoundError as e:
        print_error(str(e))
        sys.exit(1)

    news_dir = script_dir / "news"
    pull_repo(repo_root, news_dir)

    # Загрузка или автоматическое создание конфигурации зон
    if not ZONES_CONFIG_FILE.exists():
        zones_global = discover_and_save_zones(repo_root)
        print_info(f"Создан файл zones_config.json с {len(zones_global)} кластерами.")
    else:
        zones_global = load_zones()
        check_and_update_zones(repo_root, zones_global)

    all_found = True
    for zone in zones_global:
        full_path = repo_root / zone["path"]
        if not full_path.exists():
            print_error(f"Кластер {zone['label']} не найден!")
            all_found = False
    if not all_found:
        print_error("Ошибка: некоторые зоны не найдены. Продолжение невозможно.")
        sys.exit(1)

    while True:
        print("\n" + "=" * 50)
        print("Введите критерии поиска (можно оставить пустым, но хотя бы одно поле):")
        subdomain = input("  cowork_subdomain: ").strip()
        email = input("  email: ").strip()
        inn = input("  ИНН: ").strip()
        stand_name = input("  stand_name: ").strip()
        if not any([subdomain, email, inn, stand_name]):
            print_warn("Ошибка: необходимо заполнить хотя бы одно поле.")
            continue

        criteria = {'subdomain': subdomain if subdomain else None,
                    'email': email if email else None,
                    'inn': inn if inn else None,
                    'stand_name': stand_name if stand_name else None}

        results = find_stands(repo_root, criteria)
        if not results:
            print_warn("Не найдено стендов, соответствующих критериям.")
            continue

        print_info(f"Найдено {len(results)} стендов:")
        for idx, data in enumerate(results, 1):
            print(f"\n{idx}. Папка: {data.get('folder', 'N/A')}")
            print(f"   Расположение: {data.get('stand_dir', 'N/A')}")
            print(f"   stand_name: {data.get('stand_name', 'N/A')}")
            print(f"   cowork_subdomain: {data.get('cowork_subdomain', 'N/A')}")
            print(f"   admin_email: {data.get('admin_email', 'N/A')}")
            print(f"   ИНН: {data.get('inn', 'N/A')}")
            print(f"   expiry_date: {data.get('expiry_date', 'N/A')}")
            print(f"   cowork_revision: {data.get('cowork_revision', 'N/A')}")
            if data.get('broken'):
                print("   [broken stand]")

        selected = select_stand(results)
        if selected is None:
            continue

        while True:
            print_action("Выбрать действие")
            print("  1. Открыть Admin Helper и ArgoCD")
            print("  2. Зайти на тенант")
            print("  3. Открыть консоль тенантов")
            print("  4. Запустить FAQ-агента")
            print("  5. Открыть конфигурацию стенда")
            print("  0. Вернуться к поиску")
            print()
            action = input_with_timeout("Выберите действие (0-5): ", 60)
            if action is None:
                print_warn("Таймаут ожидания. Возврат к поиску.")
                break
            action = action.strip()
            if action == '0':
                break
            elif action == '1':
                open_links(selected, script_dir)
            elif action == '2':
                open_tenant(selected)
            elif action == '3':
                open_tenant_console()
            elif action == '4':
                open_faq_agent()
            elif action == '5':
                open_stand_config(selected)
            else:
                print_warn("Неверный ввод, попробуйте снова.")

if __name__ == "__main__":
    zones_global = []
    main()