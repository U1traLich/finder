from giga_finder.dsn import prompt_heimdall_url
from giga_finder.scripts import generate_fix_empty_roles_script
from giga_finder.workspaces import _run_with_dry_run

def fix_empty_roles(selected, zone):
    """Пункт 7: пользователи без глобальной роли (heimdall_auth) → роль «Участник»."""
    hm = prompt_heimdall_url(selected, zone)
    _run_with_dry_run(
        zone, "fix_empty_roles.py",
        lambda dry: generate_fix_empty_roles_script(dry, hm),
        "Скрипт с DRY_RUN=True сохранён в exec/.",
    )
