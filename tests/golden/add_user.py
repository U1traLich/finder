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

MEMBER_ROLE_ID = "workspace.member"

WORKSPACE_IDS = [
    "11111111-1111-1111-1111-111111111111"
]
USER_EMAILS = [
    "user@example.com"
]
# True — только показать, что будет сделано, ничего не менять
DRY_RUN = True

DEFAULT_YGGDRASIL_URL = "postgresql://yggdrasil:secret@yggdrasil-postgres-rw.cowork-tb01-0001.svc.cluster.local:5432/yggdrasil"
DEFAULT_HEIMDALL_URL = "postgresql://heimdall_auth:secret@heimdall-auth-postgres-rw.cowork-tb01-0001.svc.cluster.local:5432/heimdall_auth"

async def get_invite_status(heimdall_connection: asyncpg.Connection, email: str) -> str:
    row = await heimdall_connection.fetchrow(
        """
        SELECT status, expires_at
        FROM invite_tokens
        WHERE email_normalized = $1
        ORDER BY updated_at DESC
        LIMIT 1
        """,
        email.lower(),
    )
    if row is None:
        return "пользователь не приглашён"
    now = datetime.now(timezone.utc)
    expires_at = row["expires_at"]
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if row["status"] == "pending" and expires_at > now:
        return f"приглашение отправлено, не принято, ссылка живёт до {expires_at:%Y-%m-%d %H:%M} UTC"
    if expires_at <= now:
        return f"ссылка истекла {expires_at:%Y-%m-%d %H:%M} UTC, нужно перевыслать приглашение"
    return f"токен в статусе {row['status']}, пользователя в провайдере нет"

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
    if not USER_EMAILS or any("@" not in email for email in USER_EMAILS):
        print("ERROR: заполните USER_EMAILS (список email)")
        sys.exit(2)

    yggdrasil_connection = await asyncpg.connect(yggdrasil_url)
    heimdall_connection = await asyncpg.connect(heimdall_url)
    try:
        print(f"пользователи: {', '.join(USER_EMAILS)}")
        print(f"режим: {'DRY RUN — ничего не будет изменено' if DRY_RUN else 'APPLY'}")
        print()

        added = 0
        already = 0
        planned = 0
        ws_errors = 0
        missing = []
        for workspace_id in WORKSPACE_IDS:
            try:
                ws = await get_workspace_state(yggdrasil_connection, workspace_id)
            except ValueError as error:
                print(f"[SKIP] workspace_id={workspace_id}: {error}")
                ws_errors += 1
                continue

            print(f"workspace_id={workspace_id} name={ws['name']!r} type={ws['type']}")

            for email in USER_EMAILS:
                try:
                    user_id = await resolve_user_id(yggdrasil_connection, email)
                except ValueError as error:
                    print(f"  [SKIP] {email}: {error}")
                    if email not in missing:
                        missing.append(email)
                    continue

                existing_role = await get_member_role(yggdrasil_connection, workspace_id, user_id)
                existing_grant = await get_grant(heimdall_connection, user_id, workspace_id)

                print(f"  пользователь: user_id={user_id} ({email})")
                print(f"    yggdrasil role={existing_role}, heimdall grant={existing_grant}")

                if existing_role == MEMBER_ROLE_ID and existing_grant == MEMBER_ROLE_ID:
                    print("  [SKIP] пользователь уже участник с ролью member")
                    already += 1
                    print()
                    continue

                if DRY_RUN:
                    print("  план: добавить в workspace_members, workspace_favorites, access_grants (role=member)")
                    print("  DRY RUN: изменения не применены")
                    planned += 1
                    print()
                    continue

                async with yggdrasil_connection.transaction(), heimdall_connection.transaction():
                    await add_member(
                        yggdrasil_connection, heimdall_connection,
                        workspace_id, user_id,
                    )
                print("  [OK] пользователь добавлен в пространство")
                added += 1
                print()

        if DRY_RUN:
            print(f"итого: пространств={len(WORKSPACE_IDS)}, к добавлению={planned}, уже участники={already}, ошибок пространств={ws_errors}")
        else:
            print(f"итого: пространств={len(WORKSPACE_IDS)}, добавлено={added}, уже участники={already}, ошибок пространств={ws_errors}")
        if missing:
            print(f"не найдено пользователей={len(missing)}: {', '.join(missing)}")
            for email in missing:
                try:
                    status = await get_invite_status(heimdall_connection, email)
                except Exception as error:
                    status = f"статус приглашения не удалось определить ({error})"
                print(f"  {email}: {status}")
        if ws_errors or missing:
            sys.exit(1)
    finally:
        await yggdrasil_connection.close()
        await heimdall_connection.close()

if __name__ == "__main__":
    asyncio.run(main())
