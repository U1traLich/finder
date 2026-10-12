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


async def main() -> None:
    yggdrasil_url = get_database_url(("YGGDRASIL_DATABASE_URL",), "postgresql://yggdrasil:secret@yggdrasil-postgres-rw.cowork-tb01-0001.svc.cluster.local:5432/yggdrasil")

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
