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


OWNER_ROLE_ID = "workspace.owner"
MEMBER_ROLE_ID = "workspace.member"

WORKSPACE_IDS = [
    "11111111-1111-1111-1111-111111111111"
]
NEW_OWNER_EMAIL = "user@example.com"
# True — только показать, что будет сделано, ничего не менять
DRY_RUN = True

DEFAULT_YGGDRASIL_URL = "postgresql://yggdrasil:secret@yggdrasil-postgres-rw.cowork-tb01-0001.svc.cluster.local:5432/yggdrasil"
DEFAULT_HEIMDALL_URL = "postgresql://heimdall_auth:secret@heimdall-auth-postgres-rw.cowork-tb01-0001.svc.cluster.local:5432/heimdall_auth"

async def user_exists(connection: asyncpg.Connection, user_id: str) -> bool:
    return await connection.fetchval(
        "SELECT EXISTS(SELECT 1 FROM users WHERE id = $1)", user_id
    )

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
