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
# Роль «Участник» (global role id в heimdall_auth)
MEMBER_ROLE_ID = "6441ce25-27e5-436a-ae2d-374d080dcab1"
# Исключения: тестовый аккаунт и внешние пользователи не трогаем
EXCLUDE_EMAILS = ["cp-test@gigab2b.ru"]
EXTERNAL_PREFIX = "ext-"
# True — только показать, кто без ролей, ничего не менять
DRY_RUN = True
DEFAULT_HEIMDALL_URL = "postgresql://heimdall_auth:secret@heimdall-auth-postgres-rw.cowork-tb01-0001.svc.cluster.local:5432/heimdall_auth"
async def main() -> None:
    heimdall_url = get_database_url(("HEIMDALL_DATABASE_URL", "AUTH_DATABASE_URL"), DEFAULT_HEIMDALL_URL)
    connection = await asyncpg.connect(heimdall_url)
    try:
        rows = await connection.fetch(
            """
            SELECT pu.provider_user_id::text AS user_id, pu.email_normalized AS email
            FROM provider_users pu
            WHERE pu.email_normalized IS NOT NULL
              AND btrim(pu.email_normalized) <> ''
              AND lower(pu.email_normalized) <> 'cp-test@gigab2b.ru'
              AND NOT EXISTS (
                  SELECT 1
                  FROM user_global_roles ugr
                  WHERE ugr.user_id = pu.provider_user_id
              )
            ORDER BY pu.email_normalized
            """
        )
        # Фильтр ext-* на стороне Python: в DRY RUN показываем всех, но помечаем
        candidates = []
        skipped_external = 0
        for row in rows:
            email = (row["email"] or "").lower()
            if email.startswith(EXTERNAL_PREFIX):
                skipped_external += 1
                continue
            candidates.append(dict(row))
        print(f"Пользователей без глобальной роли: {len(rows)}")
        if skipped_external:
            print(f"Среди них внешних (ext-*), которые НЕ получат роль: {skipped_external}")
        if not candidates:
            print("Никому выдавать роль не нужно.")
            return
        print()
        for row in candidates:
            print(f"  {row['email']}")
            print(f"    user_id: {row['user_id']}")
        print()
        if DRY_RUN:
            print(f"[DRY RUN] Будут добавлены записи в user_global_roles: {len(candidates)} шт.")
            print("[DRY RUN] Ничего не изменено. Для боевого запуска запустите скрипт с DRY_RUN=False.")
            return
        inserted = 0
        for row in candidates:
            res = await connection.execute(
                """
                INSERT INTO user_global_roles (user_id, role_id, source, assigned_by)
                VALUES ($1, $2, 'local', 'migration:workspaces-access-backfill')
                ON CONFLICT (user_id, role_id) DO NOTHING
                """,
                row["user_id"], MEMBER_ROLE_ID,
            )
            if res == "INSERT 1":
                inserted += 1
        print(f"Добавлено записей в user_global_roles: {inserted} из {len(candidates)}.")
    finally:
        await connection.close()
if __name__ == "__main__":
    asyncio.run(main())
