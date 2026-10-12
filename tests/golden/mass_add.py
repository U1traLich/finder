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


MEMBER_ROLE_ID = "workspace.member"
WORKSPACE_IDS = [
    "11111111-1111-1111-1111-111111111111"
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
                                   "postgresql://yggdrasil:secret@yggdrasil-postgres-rw.cowork-tb01-0001.svc.cluster.local:5432/yggdrasil")
    heimdall_url = os.environ.get("HEIMDALL_DATABASE_URL",
                                  "postgresql://heimdall_auth:secret@heimdall-auth-postgres-rw.cowork-tb01-0001.svc.cluster.local:5432/heimdall_auth")

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
