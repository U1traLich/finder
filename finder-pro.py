#!/usr/bin/env python3
import subprocess, re, sys, webbrowser
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

ZONES = [
    {"path": "cce-prod/cowork/clusters/cce-prod-tb01", "label": "cce-prod-tb01", "url": "https://argocd-tb01.cowork.ru/applications/cowork/{folder}?resource="},
    {"path": "cce-prod/cowork/clusters/cce-prod-st02", "label": "cce-prod-st02", "url": "https://argocd-st02.cowork.ru/applications/cowork/{folder}?resource="},
]
CONFIG = "config.yaml"

# --- Функции цветного вывода (переиспользованы из L1) ---
def print_info(message):
    if sys.stdout.isatty():
        print(f"{WHITE}[{GREEN}INFO{WHITE}]{RESET} {message}")
    else:
        print(f"[INFO] {message}")

def print_warn(message):
    if sys.stdout.isatty():
        print(f"{WHITE}[{YELLOW}WARN{WHITE}]{RESET} {message}")
    else:
        print(f"[WARN] {message}")

def print_error(message):
    if sys.stdout.isatty():
        print(f"{WHITE}[{RED}ERROR{WHITE}]{RESET} {message}")
    else:
        print(f"[ERROR] {message}")

def print_action(message, end="\n"):
    if sys.stdout.isatty():
        print(f"{WHITE}[{CYAN}ACTION{WHITE}]{RESET} {message}", end=end, flush=True)
    else:
        print(f"[ACTION] {message}", end=end, flush=True)

def colorize_yellow(text):
    return f"{YELLOW}{text}{RESET}" if sys.stdout.isatty() else text

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
    print_info("Обновление репозитория...")
    remote_url = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=repo_root,
        capture_output=True,
        text=True
    ).stdout.strip()
    if remote_url:
        print_info(f"Удалённый репозиторий: {remote_url}")

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
        print_error("Не удалось выполнить git fetch. Возможные причины:")
        print_error("  - Нет доступа к удалённому репозиторию")
        print_error("  - Истекли учётные данные (токен/пароль)")
        print_error("  - Неверный URL (проверьте git remote -v)")
        print_error(f"stderr:\n{fetch_result.stderr}")
        sys.exit(1)

    pull_result = subprocess.run(
        ["git", "pull", "origin", "master"],
        cwd=repo_root,
        capture_output=True,
        text=True
    )
    if pull_result.returncode != 0:
        print_error("git pull завершился с ошибкой:")
        print_error(pull_result.stderr)
        sys.exit(1)

    head_after = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        capture_output=True,
        text=True
    ).stdout.strip()

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
    for zone in ZONES:
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
        print_info("Файл admin-helper-new.html открыт.")
    else:
        print_warn("admin-helper-new.html не найден.")
    url = selected['url_template'].format(folder=selected['folder'])
    webbrowser.open(url)
    print_info(f"ArgoCD открыт для стенда: {selected['folder']}")

def open_tenant(selected):
    subdomain = selected.get('cowork_subdomain')
    if not subdomain:
        print_warn("Не указан cowork_subdomain, невозможно открыть тенант.")
        return
    url = f"https://{subdomain}.cowork.ru"
    print_info(f"Вход на тенант: {url}")
    webbrowser.open(url)

def open_tenant_console():
    url = "https://tenant-console.cowork.ru/"
    print_info(f"Открытие консоли тенантов: {url}")
    webbrowser.open(url)

def open_faq_agent():
    print_info("Запуск FAQ-агента...")
    webbrowser.open(FAQ_AGENT_URL)

def main():
    script_dir = Path(__file__).parent.resolve()
    try:
        repo_root = get_repo_root(script_dir)
    except FileNotFoundError as e:
        print_error(str(e))
        sys.exit(1)

    news_dir = script_dir / "news"
    pull_repo(repo_root, news_dir)

    all_found = True
    for zone in ZONES:
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

        # Визуальный разделитель перед меню действий
        print("\n" + "=" * 50)

        while True:
            print_action("Выбрать действие")
            print("  1. Открыть Admin Helper и ArgoCD")
            print("  2. Зайти на тенант")
            print("  3. Открыть консоль тенантов")
            print("  4. Запустить FAQ-агента")
            print("  0. Вернуться к поиску")
            action = input("Выберите действие (0-4): ").strip()

            if action == '0':
                break
            elif action == '1':
                open_links(selected, script_dir)
                break
            elif action == '2':
                open_tenant(selected)
                break
            elif action == '3':
                open_tenant_console()
                break
            elif action == '4':
                open_faq_agent()
                break
            else:
                print_warn("Неверный ввод, попробуйте снова.")

if __name__ == "__main__":
    main()