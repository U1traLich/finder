# ===== Общие хелперы встроенных скриптов (импорты + DSN + запросы) =====
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
from datetime import datetime, timezone

# True — только показать, какие сессии будут сброшены, ничего не менять
DRY_RUN = True
# Порог staleness в минутах: сессия считается зависшей, если last_active_at старше этого значения.
STALE_MINUTES = 10
# Конкретный session_id для точечного сброса (None — массовый режим по порогу).
SESSION_ID = "33333333-3333-3333-3333-333333333333"
DEFAULT_YGGDRASIL_URL = "postgresql://yggdrasil:secret@yggdrasil-postgres-rw.cowork-tb01-0001.svc.cluster.local:5432/yggdrasil"

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
            print(f"    застряла: ~{int(round(stuck_for))} мин")
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
                print(f"[OK] session_id={row['id']} сброшена (застряла ~{int(round(stuck_for))} мин)")
            else:
                print(f"[SKIP] session_id={row['id']}: статус уже изменился или есть interrupt_data")
        print()
        print(f"итого: сброшено={updated}, пропущено={len(stale) - updated}")
    finally:
        await connection.close()

if __name__ == "__main__":
    asyncio.run(main())
