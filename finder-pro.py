#!/usr/bin/env python3
import subprocess, re, sys, webbrowser
from pathlib import Path
from datetime import datetime
from urllib.parse import quote

# ----- НАСТРОЙКИ -----
REPO_PATH = Path.home() / "Documents/Giga/cowork/argocd"
ZONES = [
    {"path": "cce-prod/cowork/clusters/cce-prod-tb01", "label": "cce-prod-tb01", "url": "https://argocd-tb01.cowork.ru/applications/cowork/{folder}?resource="},
    {"path": "cce-prod/cowork/clusters/cce-prod-st02", "label": "cce-prod-st02", "url": "https://argocd-st02.cowork.ru/applications/cowork/{folder}?resource="},
]
CONFIG = "config.yaml"
YELLOW = '\033[93m'
RESET = '\033[0m'

def colorize(text):
    return f"{YELLOW}{text}{RESET}" if sys.stdout.isatty() else text

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
    print("Обновление репозитория...")
    
    # Показываем URL удалённого репозитория (для диагностики)
    remote_url = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=repo_root,
        capture_output=True,
        text=True
    ).stdout.strip()
    if remote_url:
        print(f"[INFO] Удалённый репозиторий: {remote_url}")
    
    # Текущий HEAD до обновления
    head_before = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        capture_output=True,
        text=True
    ).stdout.strip()
    
    # Сначала fetch, чтобы получить свежие данные
    fetch_result = subprocess.run(
        ["git", "fetch", "origin"],
        cwd=repo_root,
        capture_output=True,
        text=True
    )
    if fetch_result.returncode != 0:
        print("[ERROR] Не удалось выполнить git fetch. Возможные причины:")
        print("  - Нет доступа к удалённому репозиторию")
        print("  - Истекли учётные данные (токен/пароль)")
        print("  - Неверный URL (проверьте git remote -v)")
        print(f"stderr:\n{fetch_result.stderr}")
        sys.exit(1)
    
    # Теперь pull (merge) с явным указанием ветки master
    pull_result = subprocess.run(
        ["git", "pull", "origin", "master"],
        cwd=repo_root,
        capture_output=True,
        text=True
    )
    if pull_result.returncode != 0:
        print("[ERROR] git pull завершился с ошибкой:")
        print(pull_result.stderr)
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
            print(f"[INFO] Полные изменения сохранены в {news_dir / fname}")
        if diff_stat:
            files = subprocess.run(
                ["git", "diff", "--name-only", head_before, head_after],
                cwd=repo_root,
                capture_output=True,
                text=True
            ).stdout.strip().split('\n')
            print("\n📂 Изменённые файлы (cowork_subdomain):")
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
                print(f"  {f} ({colorize(sub)})")
        else:
            print("Изменения затянуты, но статистика пуста.")
    else:
        print("Новых изменений нет.")

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

def update_config(file_path, key, value):
    content = file_path.read_text(encoding='utf-8')
    pattern = rf'^(\s*{key}:\s*)"?([^"\n]*)"?'
    new_content = re.sub(pattern, rf'\1"{value}"', content, flags=re.MULTILINE)
    if new_content != content:
        file_path.write_text(new_content, encoding='utf-8')
        return True
    return False

def select_stand(results):
    if len(results) == 1:
        return results[0]
    print("\nНайдено несколько стендов. Выберите нужный:")
    for i, data in enumerate(results, 1):
        sub = data.get('cowork_subdomain', 'N/A')
        print(f"  {i}. {data['folder']} ({colorize(sub)})")
    while True:
        choice = input("Введите номер стенда (0 для отмены): ").strip()
        if choice == '0':
            return None
        if choice.isdigit() and 1 <= int(choice) <= len(results):
            return results[int(choice)-1]
        print("Неверный ввод, попробуйте снова.")

def open_links(selected, script_dir):
    helper = script_dir / "admin-helper-new.html"
    if helper.exists():
        webbrowser.open(str(helper))
        print("Файл admin-helper-new.html открыт.")
    else:
        print("admin-helper-new.html не найден.")
    url = selected['url_template'].format(folder=selected['folder'])
    webbrowser.open(url)
    print(f"ArgoCD открыт для стенда: {selected['folder']}")

def open_tenant(selected):
    subdomain = selected.get('cowork_subdomain')
    if not subdomain:
        print("Не указан cowork_subdomain, невозможно открыть тенант.")
        return
    url = f"https://{subdomain}.cowork.ru"
    print(f"Вход на тенант: {url}")
    webbrowser.open(url)

def change_expiry(selected, repo_root):
    new_date = input("Enter new expiry_date (например, 2026-08-24): ").strip()
    if not new_date:
        print("Отмена.")
        return
    config_path = selected['config_path']
    if update_config(config_path, "expiry_date", new_date):
        print("[OK] expiry_date обновлён (изменение не закоммичено).")
    else:
        print("[WARN] Не удалось найти или обновить expiry_date в файле.")

def change_admin(selected, repo_root):
    new_email = input("Enter new admin_email: ").strip()
    if not new_email:
        print("Отмена.")
        return
    config_path = selected['config_path']
    if update_config(config_path, "admin_email", new_email):
        print("[OK] admin_email обновлён (изменение не закоммичено).")
    else:
        print("[WARN] Не удалось найти или обновить admin_email в файле.")

def has_uncommitted_changes(repo_root):
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo_root,
        capture_output=True,
        text=True
    )
    return bool(result.stdout.strip())

def commit_and_push(repo_root, folder_name, message):
    # Сохраняем текущие изменения (stash), если они есть
    print("Сохранение текущих изменений (stash)...")
    stash_result = subprocess.run(["git", "stash"], cwd=repo_root, capture_output=True, text=True)
    if stash_result.returncode != 0:
        print("[WARN] Не удалось сохранить изменения в stash. Возможно, изменений нет.")
        print("Пропускаем stash, так как изменений нет.")
    else:
        print("Stash сохранён.")

    # Переключаемся на master и обновляем
    print("Переключение на master и обновление...")
    subprocess.run(["git", "checkout", "master"], cwd=repo_root, check=True, capture_output=True)
    subprocess.run(["git", "pull", "origin", "master"], cwd=repo_root, check=True, capture_output=True)

    # Применяем сохранённые изменения (stash pop), если stash был создан
    if stash_result.returncode == 0:
        print("Применение сохранённых изменений...")
        pop_result = subprocess.run(["git", "stash", "pop"], cwd=repo_root, capture_output=True, text=True)
        if pop_result.returncode != 0:
            print("[ERROR] Конфликты при применении stash. Вывод:")
            print(pop_result.stdout)
            print(pop_result.stderr)
            print("Разрешите конфликты вручную и повторите попытку.")
            return

    # Формируем имя ветки с timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    branch_name = f"feature/update-{folder_name}-{timestamp}"

    print(f"\nСоздание ветки {branch_name}...")
    subprocess.run(["git", "checkout", "-b", branch_name], cwd=repo_root, check=True, capture_output=True)
    print("Добавление изменений...")
    subprocess.run(["git", "add", "."], cwd=repo_root, check=True, capture_output=True)
    print(f"Коммит с сообщением: {message}")
    subprocess.run(["git", "commit", "-m", message], cwd=repo_root, check=True, capture_output=True)
    print(f"Пуш ветки {branch_name}...")
    subprocess.run(["git", "push", "-u", "origin", branch_name], cwd=repo_root, check=True, capture_output=True)
    print(f"[OK] Изменения запушены в ветку {branch_name}")

    encoded_title = quote(message)
    encoded_desc = quote(message)
    mr_url = (f"https://git.sberdevices.ru/gigabb/infra/cowork/argocd/-/merge_requests/new?"
              f"merge_request%5Bsource_branch%5D={branch_name}&"
              f"merge_request%5Btarget_branch%5D=master&"
              f"merge_request%5Btitle%5D={encoded_title}&"
              f"merge_request%5Bdescription%5D={encoded_desc}&"
              f"merge_request%5Bremove_source_branch%5D=true")
    print(f"Для создания Merge Request перейдите по ссылке:")
    print(mr_url)

def main():
    script_dir = Path(__file__).parent.resolve()
    try:
        repo_root = get_repo_root(script_dir)
    except FileNotFoundError as e:
        print(e)
        sys.exit(1)

    news_dir = script_dir / "news"
    pull_repo(repo_root, news_dir)  # теперь при ошибке скрипт завершится

    all_found = True
    for zone in ZONES:
        full_path = repo_root / zone["path"]
        if not full_path.exists():
            print(f"[ERROR] Кластер {zone['label']} не найден!")
            all_found = False
    if not all_found:
        print("Ошибка: некоторые зоны не найдены. Продолжение невозможно.")
        sys.exit(1)

    while True:
        print("\n" + "=" * 50)
        print("Введите критерии поиска (можно оставить пустым, но хотя бы одно поле):")
        subdomain = input("  cowork_subdomain: ").strip()
        email = input("  email: ").strip()
        inn = input("  ИНН: ").strip()
        stand_name = input("  stand_name: ").strip()
        if not any([subdomain, email, inn, stand_name]):
            print("Ошибка: необходимо заполнить хотя бы одно поле.")
            continue

        criteria = {'subdomain': subdomain if subdomain else None,
                    'email': email if email else None,
                    'inn': inn if inn else None,
                    'stand_name': stand_name if stand_name else None}

        results = find_stands(repo_root, criteria)
        if not results:
            print("Не найдено стендов, соответствующих критериям.")
            continue

        print(f"Найдено {len(results)} стендов:")
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
            print("\nДействия для стенда:", selected['folder'])
            print("  1. Открыть Admin Helper и ArgoCD")
            print("  2. Изменить срок действия (expiry_date)")
            print("  3. Изменить администратора (admin_email)")
            print("  4. Зайти на тенант")
            print("  0. Вернуться к поиску")
            action = input("Выберите действие (0-4): ").strip()

            if action == '0':
                break
            elif action == '1':
                open_links(selected, script_dir)
                break
            elif action == '2':
                change_expiry(selected, repo_root)
                break
            elif action == '3':
                change_admin(selected, repo_root)
                break
            elif action == '4':
                open_tenant(selected)
                break
            else:
                print("Неверный ввод, попробуйте снова.")

        if has_uncommitted_changes(repo_root):
            print("\nОбнаружены незакоммиченные изменения в репозитории.")
            commit_choice = input("Хотите закоммитить и запушить изменения? (y/n): ").strip().lower()
            if commit_choice == 'y':
                commit_msg = input("Напишите коммит с понятным описанием: ").strip()
                if commit_msg:
                    try:
                        commit_and_push(repo_root, selected['folder'], commit_msg)
                    except subprocess.CalledProcessError as e:
                        print(f"[ERROR] Ошибка при выполнении Git: {e.stderr.decode().strip()}")
                else:
                    print("Сообщение коммита не может быть пустым. Пропускаем.")
            else:
                print("Изменения остались незакоммиченными.")
        else:
            print("Изменений нет.")

if __name__ == "__main__":
    main()
