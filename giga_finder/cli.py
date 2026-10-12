import sys

from giga_finder.accounts import create_stand_accounts
from giga_finder.argocd import _argocd_base
from giga_finder.constants import *
from giga_finder.repo import _init_environment, check_and_notify
from giga_finder.roles import fix_empty_roles
from giga_finder.runtime import open_file, open_url, select_runtime_mode
from giga_finder.search import _print_stand, find_stands, load_stand_index, select_stand
from giga_finder.sessions import reset_stale_sessions
from giga_finder.tokens import langfuse_tokens, token_utilization_report
from giga_finder.helptext import (
    print_main_help, print_search_help, print_stand_menu_help, print_token_help, print_workspace_help,
)
from giga_finder.ui import (
    colorize, input_with_timeout, is_help_request, print_action, print_hint, print_info,
    print_logo, print_warn,
)
from giga_finder.workspaces import (
    ws_add_base_spaces, ws_add_user, ws_change_owner, ws_list_workspaces,
    ws_mass_add, ws_remove_user,
)
from giga_finder.zones import resolve_zone

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
        print_hint()
        choice = input_with_timeout("Выберите функцию: ", INPUT_TIMEOUT)
        if choice is None:
            break
        choice = choice.strip()
        if choice == "0":
            break
        if is_help_request(choice):
            print_workspace_help()
            continue
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
        print_hint()
        print()
        action = input_with_timeout("Выберите действие (0-10): ", INPUT_TIMEOUT)
        if action is None:
            print_warn("Таймаут ожидания. Возврат к поиску.")
            break
        action = action.strip()
        if action == "0":
            break
        if is_help_request(action):
            print_stand_menu_help()
            continue
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

def _prompt_search_criteria():
    while True:
        print("\n" + "=" * 50)
        print("Введите критерии поиска (заполни хотя бы одно поле):")
        print_hint("? в любом поле — подсказка")
        subdomain = input("  cowork_subdomain: ").strip()
        email = input("  email: ").strip()
        inn = input("  ИНН: ").strip()
        stand_name = input("  stand_name: ").strip()
        if any(is_help_request(value) for value in (subdomain, email, inn, stand_name)):
            print_search_help()
            continue
        if not any((subdomain, email, inn, stand_name)):
            print_warn("Ошибка: необходимо заполнить хотя бы одно поле.")
            return None
        return {
            "subdomain": subdomain or None,
            "email": email or None,
            "inn": inn or None,
            "stand_name": stand_name or None,
        }


def _requested_help(argv):
    args = [item.lower() for item in argv]
    if not args:
        return None
    if args[0] == "token" and any(item in ("-h", "--help", "help") for item in args[1:]):
        return "token"
    if args[0] in ("-h", "--help", "help"):
        if len(args) > 1 and args[1] == "token":
            return "token"
        return "main"
    return None


def _handle_keyboard_interrupt():
    print("\nВыполнение остановлено пользователем.")
    raise SystemExit(130)

def main():
    topic = _requested_help(sys.argv[1:])
    if topic == "token":
        print_token_help()
        return
    if topic == "main":
        print_main_help()
        return
    print_logo()
    select_runtime_mode()
    script_dir = ROOT
    # Режим запуска: без аргумента — сразу поиск стендов (старый вид),
    # «token» — сразу подсчёт утилизации токенов.
    mode = (sys.argv[1].strip().lower() if len(sys.argv) > 1 else "")
    repo_root, zones = _init_environment(script_dir)
    if mode == "token":
        token_utilization_report(repo_root, script_dir, zones)
        return
    stand_rows = load_stand_index(repo_root, zones)
    while True:
        # Фоновая проверка (раз в 15 минут): уведомляем только в простое,
        # перед экраном критериев поиска — во время работы в меню не мешаем.
        if check_and_notify(repo_root, script_dir / "news", zones):
            stand_rows = load_stand_index(repo_root, zones)
        criteria = _prompt_search_criteria()
        if criteria is None:
            continue
        results = find_stands(repo_root, zones, criteria, rows=stand_rows)
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
