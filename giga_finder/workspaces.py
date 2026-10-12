import re

from giga_finder.argocd import open_workspace_exec
from giga_finder.constants import *
from giga_finder.dsn import prompt_db_urls
from giga_finder.scripts import (
    generate_add_user_script,
    generate_change_owner_script,
    generate_list_workspaces_script,
    generate_mass_add_script,
    generate_remove_user_script,
)
from giga_finder.ui import action_label, colorize, is_help_request, print_info, print_warn

def prompt_workspace_ids():
    while True:
        print_info("Введите ID пространств через запятую (ID — из URL /workspaces/<ID>):")
        raw = input(f"{action_label()} ID: ").strip()
        if is_help_request(raw):
            from giga_finder.helptext import print_workspace_help
            print_workspace_help()
            continue
        return [x.strip() for x in re.split(r"[,\s]+", raw) if x.strip()]

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
