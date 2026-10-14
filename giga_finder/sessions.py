from giga_finder.constants import INPUT_TIMEOUT
from giga_finder.dsn import prompt_db_urls
from giga_finder.scripts import generate_reset_stale_sessions_script
from giga_finder.ui import action_label, input_with_timeout, is_help_request, print_error, print_hint, print_info, print_warn
from giga_finder.workspaces import _run_with_dry_run

def reset_stale_sessions(selected, zone):
    """Пункт 10: сброс зависших сессий (running → active) в БД Yggdrasil."""
    print_info("Выберите режим сброса:")
    print("  1. Все зависшие сессии (stale > 10 минут)")
    print("  2. Конкретная сессия по ID")
    print_hint()
    while True:
        choice = input_with_timeout("Режим (1/2): ", INPUT_TIMEOUT)
        if choice is None:
            print_warn("Таймаут ожидания. Операция отменена.")
            return
        choice = choice.strip()
        if is_help_request(choice):
            from giga_finder.helptext import print_sessions_help
            print_sessions_help()
            continue
        break
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
