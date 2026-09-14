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
VERSION = "v2.0.0"
ZONES_DIR = Path(__file__).parent / "zones"
ZONES_CONFIG_FILE = ZONES_DIR / "zones_config.json"
EXEC_DIR = Path(__file__).parent / "exec"

# --- Управление пространствами: зоны и ArgoCD ---
# Зоны, доступные в подменю «Управление пространствами».
# code — суффикс ArgoCD (argocd-<code>.cowork.ru); cluster — имя кластера в репозитории.
WORKSPACE_ZONES = [
    {"code": "st02", "cluster": "cce-prod-st02"},
    {"code": "tb01", "cluster": "cce-prod-tb01"},
    {"code": "tb04", "cluster": "cce-prod-tb04"},
]

# Имя приложения ArgoCD, в котором живёт управляющий под debug-ubuntu.
ARGOCD_APP = "cowork/cowork"
# Подстрока имени пода управляющего контейнера.
DEBUG_POD_PREFIX = "debug-ubuntu"

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

def colorize_cyan(text):
    return f"{CYAN}{text}{RESET}" if sys.stdout.isatty() else text

def action_label():
    """Подсветка '[ACTION]' для промптов ввода — как у print_action."""
    return f"{WHITE}[{CYAN}ACTION{WHITE}]{RESET}" if sys.stdout.isatty() else "[ACTION]"

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
    print(f"  Версия: {VERSION}")

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
    ZONES_DIR.mkdir(parents=True, exist_ok=True)
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

# --- Управление пространствами: шаблоны скриптов ---
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
        "SELECT id::text FROM users WHERE email = $1", email
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

import asyncpg

MEMBER_ROLE_ID = "workspace.member"
WORKSPACE_NAMESPACE = "workspace"

WORKSPACE_IDS = [
__IDS__
]
USER_EMAIL = __EMAIL__
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
        "SELECT id::text FROM users WHERE email = $1", email
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
    if not USER_EMAIL or "@" not in USER_EMAIL:
        print("ERROR: заполните USER_EMAIL")
        sys.exit(2)

    yggdrasil_connection = await asyncpg.connect(yggdrasil_url)
    heimdall_connection = await asyncpg.connect(heimdall_url)
    try:
        user_id = await resolve_user_id(yggdrasil_connection, USER_EMAIL)
        print(f"пользователь: {USER_EMAIL} (user_id={user_id})")
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

            existing_role = await get_member_role(yggdrasil_connection, workspace_id, user_id)
            existing_grant = await get_grant(heimdall_connection, user_id, workspace_id)

            print(f"workspace_id={workspace_id} name={ws['name']!r} type={ws['type']}")
            print(f"  пользователь: user_id={user_id} ({USER_EMAIL})")
            print(f"    yggdrasil role={existing_role}, heimdall grant={existing_grant}")

            if existing_role == MEMBER_ROLE_ID and existing_grant == MEMBER_ROLE_ID:
                print("  [SKIP] пользователь уже участник с ролью member")
                print()
                continue

            if DRY_RUN:
                print("  план: добавить в workspace_members, workspace_favorites, access_grants (role=member)")
                print("  DRY RUN: изменения не применены")
                print()
                continue

            async with yggdrasil_connection.transaction(), heimdall_connection.transaction():
                await add_member(
                    yggdrasil_connection, heimdall_connection,
                    workspace_id, user_id,
                )
            print("  [OK] пользователь добавлен в пространство")
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


def generate_change_owner_script(workspace_ids, new_owner_email, dry_run, ygg_url, hm_url):
    ids_block = ",\n".join(f'    "{w}"' for w in workspace_ids)
    return (_CHANGE_OWNER_TEMPLATE
            .replace("__IDS__", ids_block)
            .replace("__EMAIL__", json.dumps(new_owner_email))
            .replace("__DRY__", "True" if dry_run else "False")
            .replace("__YGG__", json.dumps(ygg_url))
            .replace("__HM__", json.dumps(hm_url)))


def generate_mass_add_script(workspace_ids, ygg_url, hm_url):
    ids_block = ",\n".join(f'    "{w}"' for w in workspace_ids)
    return (_MASS_ADD_TEMPLATE
            .replace("__IDS__", ids_block)
            .replace("__YGG__", json.dumps(ygg_url))
            .replace("__HM__", json.dumps(hm_url)))


def generate_add_user_script(workspace_ids, user_email, dry_run, ygg_url, hm_url):
    ids_block = ",\n".join(f'    "{w}"' for w in workspace_ids)
    return (_ADD_USER_TEMPLATE
            .replace("__IDS__", ids_block)
            .replace("__EMAIL__", json.dumps(user_email))
            .replace("__DRY__", "True" if dry_run else "False")
            .replace("__YGG__", json.dumps(ygg_url))
            .replace("__HM__", json.dumps(hm_url)))


def generate_list_workspaces_script(ygg_url):
    return _LIST_WORKSPACES_TEMPLATE.replace("__YGG__", json.dumps(ygg_url))


def build_db_url(user, host, folder, db):
    # Шаблон с полным DNS-суффиксом неймспейса стенда (неймспейс = имя папки стенда).
    return f"postgresql://{user}:<ПАРОЛЬ>@{host}.{folder}.svc.cluster.local:5432/{db}"


# --- Управление пространствами: ArgoCD ---
def open_pod_exec(zone_code, stand_name, app_suffix):
    """Открывает страницу приложения стенда (yggdrasil / heimdall) в ArgoCD.

    stand_name — имя папки/неймспейса стенда из результата поиска (например
    cowork-st02-0270). app_suffix — "yggdrasil" или "heimdall". Под в списке
    приложения выбирается вручную, вкладка exec."""
    base = f"https://argocd-{zone_code}.cowork.ru"
    app_path = f"{stand_name}/{stand_name}-{app_suffix}"
    url = f"{base}/applications/{app_path}?resource="
    webbrowser.open(url)
    print_info(f"В списке приложения выберите под {app_suffix}* и откройте вкладку exec.")


def open_workspace_exec(zone_code, script_content, script_name):
    """Сохраняет скрипт в папку exec/ рядом с finder и открывает страницу приложения
    ArgoCD, где под debug-ubuntu выбирается вручную (вкладка exec)."""
    EXEC_DIR.mkdir(parents=True, exist_ok=True)
    out = EXEC_DIR / script_name
    out.write_text(script_content, encoding="utf-8")
    print_info(f"Скрипт сохранён: {out}")

    base = f"https://argocd-{zone_code}.cowork.ru"
    url = f"{base}/applications/{ARGOCD_APP}?resource="
    webbrowser.open(url)
    print_info(f"В списке приложения выберите под {DEBUG_POD_PREFIX}* (вкладка exec), "
               f"скопируйте содержимое {script_name} в веб-терминал и запустите: python3 {script_name}")


# --- Управление пространствами: ввод параметров ---
def prompt_workspace_ids():
    print_info("Введите ID пространств через запятую (ID — из URL /workspaces/<ID>):")
    raw = input(f"{action_label()} ID: ").strip()
    return [x.strip() for x in re.split(r"[,\s]+", raw) if x.strip()]


def _extract_dsn(raw):
    """Извлекает DSN из строки результата `env | grep ...` вида
    'YGGDRASIL_DATABASE_URL=postgresql://...' — отбрасывает часть до первого '='.
    Если введено сразу DSN без префикса — принимается как есть."""
    value = raw.strip()
    if not value:
        return None
    # Убираем кавычки, если пользователь скопировал с ними.
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1].strip()
    # Отбрасываем префикс 'ИМЯ_ПЕРЕМЕННОЙ=' (если строка не DSN сама по себе).
    if not value.startswith(("postgresql://", "postgres://")) and "=" in value:
        value = value.split("=", 1)[1].strip()
    if not (value.startswith("postgresql://") or value.startswith("postgres://")):
        return None
    return value


def _expand_dns_suffix(url, folder):
    """Дополняет хост DSN полным DNS-суффиксом неймспейса стенда (folder = stand_name).

    Поды приложений отдают короткое имя сервиса (например yggdrasil-postgres-rw) —
    оно резолвится только изнутри неймспейса стенда. Скрипт запускается из
    debug-пода в другом неймспейсе, поэтому суффикс добавляется всегда:
    yggdrasil-postgres-rw -> yggdrasil-postgres-rw.<stand_name>.svc.cluster.local.
    Это штатное действие, не предупреждение."""
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


def prompt_db_urls(selected, zone, need_heimdall=True):
    folder = selected.get("folder", "")
    stand_name = selected.get("stand_name") or folder

    print_info("Открываю приложение yggdrasil в ArgoCD — скопируйте YGGDRASIL_DATABASE_URL.")
    open_pod_exec(zone["code"], stand_name, "yggdrasil")
    ygg_raw = input(f"{action_label()} Вставьте строку целиком (в поде: {colorize_cyan('env | grep YGGDRASIL_DATABASE_URL')}): ").strip()

    hm_raw = ""
    if need_heimdall:
        print_info("Открываю приложение heimdall в ArgoCD — скопируйте AUTH_DATABASE_URL.")
        open_pod_exec(zone["code"], stand_name, "heimdall")
        hm_raw = input(f"{action_label()} Вставьте строку целиком (в поде: {colorize_cyan('env | grep AUTH_DATABASE_URL')}): ").strip()

    ygg = _extract_dsn(ygg_raw)
    if ygg_raw and ygg is None:
        print_warn("Строка не распознана (ожидается 'ИМЯ=postgresql://...'). Вставлен шаблон с <ПАРОЛЬ>.")
    if ygg is None:
        ygg = build_db_url("yggdrasil", "yggdrasil-postgres-rw", folder, "yggdrasil")
        if not ygg_raw:
            print_warn("YGGDRASIL_DATABASE_URL не задан — вставлен шаблон с <ПАРОЛЬ>, заполните в скрипте.")
    else:
        ygg = _expand_dns_suffix(ygg, folder)

    hm = None
    if need_heimdall:
        hm = _extract_dsn(hm_raw)
        if hm_raw and hm is None:
            print_warn("Строка не распознана (ожидается 'ИМЯ=postgresql://...'). Вставлен шаблон с <ПАРОЛЬ>.")
        if hm is None:
            hm = build_db_url("heimdall_auth", "heimdall-auth-postgres-rw", folder, "heimdall_auth")
            if not hm_raw:
                print_warn("HEIMDALL_DATABASE_URL не задан — вставлен шаблон с <ПАРОЛЬ>, заполните в скрипте.")
        else:
            hm = _expand_dns_suffix(hm, folder)

    return ygg, hm


def resolve_zone(selected):
    """Определяет зону автоматически из результата поиска (stand_dir = cce-prod-st02)."""
    cluster = selected.get("stand_dir", "")
    for z in WORKSPACE_ZONES:
        if z["cluster"] == cluster:
            return z
    # Фолбэк: ищем по коду (st02, tb01, tb04) в конце stand_dir
    code = cluster.rsplit("-", 1)[-1] if cluster else ""
    for z in WORKSPACE_ZONES:
        if z["code"] == code:
            return z
    return None


def ws_change_owner(selected, zone):
    ids = prompt_workspace_ids()
    if not ids:
        print_warn("Не введено ни одного ID, операция отменена.")
        return
    email = input("  Email нового владельца: ").strip()
    if not email or "@" not in email:
        print_warn("Некорректный email нового владельца, операция отменена.")
        return
    ygg, hm = prompt_db_urls(selected, zone)

    dry = input(f"{action_label()} Режим DRY RUN? ({colorize_cyan('yes')}/{colorize_cyan('no')}, по умолчанию yes): ").strip().lower()

    if dry in ("no", "n"):
        # Сразу боевой режим — подтверждаем.
        confirm = input(f"{action_label()} Запустить скрипт в боевом режиме (изменения будут применены)? ({colorize_cyan('yes')}/{colorize_cyan('no')}): ").strip().lower()
        if confirm not in ("yes", "y"):
            print_warn("Боевой запуск отменён, возврат в меню.")
            return
        dry_run = False
    else:
        dry_run = True

    script = generate_change_owner_script(ids, email, dry_run, ygg, hm)
    open_workspace_exec(zone["code"], script, "change_workspace_owner.py")

    # После DRY RUN — предлагаем доделать в боевом режиме.
    if dry_run:
        print_info("После сверки вывода DRY RUN — запустите боевой режим.")
        apply_now = input(f"{action_label()} Запустить в боевом режиме сейчас? ({colorize_cyan('yes')}/{colorize_cyan('no')}): ").strip().lower()
        if apply_now in ("yes", "y"):
            script_apply = generate_change_owner_script(ids, email, False, ygg, hm)
            open_workspace_exec(zone["code"], script_apply, "change_workspace_owner.py")
        else:
            print_info("Скрипт с DRY_RUN=True сохранён в exec/. Для боевого запуска повторите операцию.")


def ws_mass_add(selected, zone):
    print_warn("Скрипт идемпотентен, но без dry run — запуск сразу применяет изменения.")
    ids = prompt_workspace_ids()
    if not ids:
        print_warn("Не введено ни одного ID, операция отменена.")
        return
    ygg, hm = prompt_db_urls(selected, zone)
    confirm = input(f"{action_label()} Запустить в боевом режиме (изменения будут применены)? ({colorize_cyan('yes')}/{colorize_cyan('no')}): ").strip().lower()
    if confirm not in ("yes", "y"):
        print_warn("Запуск отменён, возврат в меню.")
        return
    script = generate_mass_add_script(ids, ygg, hm)
    open_workspace_exec(zone["code"], script, "add_all_users_to_workspace.py")


def ws_add_user(selected, zone):
    ids = prompt_workspace_ids()
    if not ids:
        print_warn("Не введено ни одного ID, операция отменена.")
        return
    email = input("  Email пользователя: ").strip()
    if not email or "@" not in email:
        print_warn("Некорректный email, операция отменена.")
        return
    ygg, hm = prompt_db_urls(selected, zone)

    dry = input(f"{action_label()} Режим DRY RUN? ({colorize_cyan('yes')}/{colorize_cyan('no')}, по умолчанию yes): ").strip().lower()

    if dry in ("no", "n"):
        # Сразу боевой режим — подтверждаем.
        confirm = input(f"{action_label()} Запустить скрипт в боевом режиме (изменения будут применены)? ({colorize_cyan('yes')}/{colorize_cyan('no')}): ").strip().lower()
        if confirm not in ("yes", "y"):
            print_warn("Боевой запуск отменён, возврат в меню.")
            return
        dry_run = False
    else:
        dry_run = True

    script = generate_add_user_script(ids, email, dry_run, ygg, hm)
    open_workspace_exec(zone["code"], script, "add_user_to_workspace.py")

    # После DRY RUN — предлагаем доделать в боевом режиме.
    if dry_run:
        print_info("После сверки вывода DRY RUN — запустите боевой режим.")
        apply_now = input(f"{action_label()} Запустить в боевом режиме сейчас? ({colorize_cyan('yes')}/{colorize_cyan('no')}): ").strip().lower()
        if apply_now in ("yes", "y"):
            script_apply = generate_add_user_script(ids, email, False, ygg, hm)
            open_workspace_exec(zone["code"], script_apply, "add_user_to_workspace.py")
        else:
            print_info("Скрипт с DRY_RUN=True сохранён в exec/.")


def ws_list_workspaces(selected, zone):
    ygg, _ = prompt_db_urls(selected, zone, need_heimdall=False)
    script = generate_list_workspaces_script(ygg)
    open_workspace_exec(zone["code"], script, "list_workspaces.py")


def workspace_menu(selected):
    while True:
        print_action("Управление пространствами")
        print("  1. Сменить владельца пространства")
        print("  2. Добавить пользователя в пространство")
        print("  3. Массовое добавление в пространство")
        print("  4. Показать все пространства на стенде")
        print("  0. Назад")
        choice = input_with_timeout("Выберите функцию: ", 60)
        if choice is None:
            break
        choice = choice.strip()
        if choice == '0':
            break
        elif choice == '1':
            zone = resolve_zone(selected)
            if zone:
                ws_change_owner(selected, zone)
            else:
                print_warn(f"Не удалось определить зону из stand_dir={selected.get('stand_dir', '?')}.")
        elif choice == '2':
            zone = resolve_zone(selected)
            if zone:
                ws_add_user(selected, zone)
            else:
                print_warn(f"Не удалось определить зону из stand_dir={selected.get('stand_dir', '?')}.")
        elif choice == '3':
            zone = resolve_zone(selected)
            if zone:
                ws_mass_add(selected, zone)
            else:
                print_warn(f"Не удалось определить зону из stand_dir={selected.get('stand_dir', '?')}.")
        elif choice == '4':
            zone = resolve_zone(selected)
            if zone:
                ws_list_workspaces(selected, zone)
            else:
                print_warn(f"Не удалось определить зону из stand_dir={selected.get('stand_dir', '?')}.")
        else:
            print_warn("Неверный ввод.")


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
            print("  6. Управление пространствами")
            print("  0. Вернуться к поиску")
            print()
            action = input_with_timeout("Выберите действие (0-6): ", 60)
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
            elif action == '6':
                workspace_menu(selected)
            else:
                print_warn("Неверный ввод, попробуйте снова.")

if __name__ == "__main__":
    zones_global = []
    main()
