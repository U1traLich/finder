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
WORKSPACE_IDS = [
    "11111111-1111-1111-1111-111111111111"
]
USER_EMAILS = [
    "user@example.com"
]
# Дополнительные user_id (UUID), которые нужно проверить/удалить.
# Заполните, если пользователь не находится по email (осиротевшие гранты).
EXTRA_USER_IDS = [
    "22222222-2222-2222-2222-222222222222"
]
# True — только показать, что будет сделано, ничего не менять
DRY_RUN = True
DEFAULT_YGGDRASIL_URL = "postgresql://yggdrasil:secret@yggdrasil-postgres-rw.cowork-tb01-0001.svc.cluster.local:5432/yggdrasil"
DEFAULT_HEIMDALL_URL = "postgresql://heimdall_auth:secret@heimdall-auth-postgres-rw.cowork-tb01-0001.svc.cluster.local:5432/heimdall_auth"
async def fetch_user_ids_by_email(connection: asyncpg.Connection, email: str) -> list[str]:
    """Все id из users по email (в одной таблице может быть несколько строк)."""
    try:
        rows = await connection.fetch(
            "SELECT id::text FROM users WHERE lower(email) = lower($1)", email
        )
    except Exception:
        return []
    return [r["id"] for r in rows]
async def resolve_user_ids(
    yggdrasil_connection: asyncpg.Connection,
    heimdall_connection: asyncpg.Connection,
    email: str,
) -> list[str]:
    """Все user_id по email: из users (yggdrasil) и из users (heimdall_auth, если есть).
    Плюс — все user_id, у которых есть access_grants на эти пространства,
    и email которых совпадает в любой из таблиц users (ловит legacy-гранты,
    привязанные к id, который по email не находится)."""
    ids = []
    for conn in (yggdrasil_connection, heimdall_connection):
        for uid in await fetch_user_ids_by_email(conn, email):
            if uid not in ids:
                ids.append(uid)
    # Доп. источник: user_id из access_grants по пространствам.
    try:
        rows = await heimdall_connection.fetch(
            """
            SELECT DISTINCT user_id::text AS uid
            FROM access_grants
            WHERE namespace = $1 AND resource_id = ANY($2)
            """,
            WORKSPACE_NAMESPACE, WORKSPACE_IDS,
        )
        for r in rows:
            uid = r["uid"]
            if uid in ids:
                continue
            # Проверяем, что это наш email (в любой из таблиц users).
            is_ours = False
            for conn in (yggdrasil_connection, heimdall_connection):
                try:
                    row = await conn.fetchrow(
                        "SELECT 1 FROM users WHERE id = $1 AND lower(email) = lower($2)",
                        uid, email,
                    )
                except Exception:
                    row = None
                if row is not None:
                    is_ours = True
                    break
            if is_ours:
                ids.append(uid)
    except Exception:
        pass
    return ids
async def remove_member(
    yggdrasil_connection: asyncpg.Connection,
    heimdall_connection: asyncpg.Connection,
    workspace_id: str,
    user_id: str,
) -> None:
    # 1) Yggdrasil: workspace_members
    await yggdrasil_connection.execute(
        """
        DELETE FROM workspace_members
        WHERE workspace_id = $1 AND user_id = $2
        """,
        workspace_id, user_id,
    )
    # 2) Yggdrasil: workspace_favorites
    await yggdrasil_connection.execute(
        """
        DELETE FROM workspace_favorites
        WHERE workspace_id = $1 AND user_id = $2
        """,
        workspace_id, user_id,
    )
    # 3) Heimdall: access_grants
    await heimdall_connection.execute(
        """
        DELETE FROM access_grants
        WHERE user_id = $1
          AND namespace = $2
          AND resource_id = $3
        """,
        user_id, WORKSPACE_NAMESPACE, workspace_id,
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
    import uuid as _uuid
    for uid in EXTRA_USER_IDS:
        try:
            _uuid.UUID(uid)
        except ValueError:
            print(f"ERROR: EXTRA_USER_IDS содержит не-UUID: {uid}")
            sys.exit(2)
    yggdrasil_connection = await asyncpg.connect(yggdrasil_url)
    heimdall_connection = await asyncpg.connect(heimdall_url)
    try:
        print(f"пользователи: {', '.join(USER_EMAILS)}")
        print(f"режим: {'DRY RUN — ничего не будет изменено' if DRY_RUN else 'APPLY'}")
        print()
        removed = 0
        not_member = 0
        planned = 0
        owners = 0
        ws_errors = 0
        missing = []
        seen_uids = set()
        for workspace_id in WORKSPACE_IDS:
            try:
                ws = await get_workspace_state(yggdrasil_connection, workspace_id)
            except ValueError as error:
                print(f"[SKIP] workspace_id={workspace_id}: {error}")
                ws_errors += 1
                continue
            print(f"workspace_id={workspace_id} name={ws['name']!r} type={ws['type']}")
            for email in USER_EMAILS:
                user_ids = await resolve_user_ids(yggdrasil_connection, heimdall_connection, email)
                for uid in user_ids:
                    seen_uids.add(uid)
                if not user_ids:
                    print(f"  [SKIP] {email}: пользователь не найден в users (yggdrasil/heimdall_auth)")
                    if email not in missing:
                        missing.append(email)
                    continue
                # Защита: владельца удалить нельзя — сначала смените владельца.
                if ws["owner_id"] in user_ids:
                    print(f"  [SKIP] {email}: пользователь — владелец пространства, сначала смените владельца")
                    owners += 1
                    print()
                    continue
                print(f"  пользователь: {email} (user_id: {', '.join(user_ids)})")
                any_role = None
                any_grant = None
                for user_id in user_ids:
                    role = await get_member_role(yggdrasil_connection, workspace_id, user_id)
                    grant = await get_grant(heimdall_connection, user_id, workspace_id)
                    print(f"    user_id={user_id}: yggdrasil role={role}, heimdall grant={grant}")
                    if role is not None:
                        any_role = role
                    if grant is not None:
                        any_grant = grant
                if any_role is None and any_grant is None:
                    print("  [SKIP] пользователь не является участником пространства")
                    not_member += 1
                    print()
                    continue
                if DRY_RUN:
                    print("  план: удалить из workspace_members, workspace_favorites, access_grants (по всем user_id)")
                    print("  DRY RUN: изменения не применены")
                    planned += 1
                    print()
                    continue
                async with yggdrasil_connection.transaction(), heimdall_connection.transaction():
                    for user_id in user_ids:
                        await remove_member(
                            yggdrasil_connection, heimdall_connection,
                            workspace_id, user_id,
                        )
                print("  [OK] пользователь удалён из пространства")
                removed += 1
                print()
            # Дополнительные user_id (вручную из чекера)
            for extra_uid in EXTRA_USER_IDS:
                if extra_uid in seen_uids:
                    continue
                seen_uids.add(extra_uid)
                if ws["owner_id"] == extra_uid:
                    print(f"  [SKIP] user_id={extra_uid}: владелец пространства, сначала смените владельца")
                    owners += 1
                    print()
                    continue
                role = await get_member_role(yggdrasil_connection, workspace_id, extra_uid)
                grant = await get_grant(heimdall_connection, extra_uid, workspace_id)
                print(f"  пользователь: (user_id={extra_uid}, из EXTRA_USER_IDS)")
                print(f"    yggdrasil role={role}, heimdall grant={grant}")
                if role is None and grant is None:
                    print("  [SKIP] записей нет")
                    not_member += 1
                    print()
                    continue
                if DRY_RUN:
                    print("  план: удалить из workspace_members, workspace_favorites, access_grants")
                    print("  DRY RUN: изменения не применены")
                    planned += 1
                    print()
                    continue
                async with yggdrasil_connection.transaction(), heimdall_connection.transaction():
                    await remove_member(
                        yggdrasil_connection, heimdall_connection,
                        workspace_id, extra_uid,
                    )
                print("  [OK] пользователь удалён из пространства")
                removed += 1
                print()
        if DRY_RUN:
            print(f"итого: пространств={len(WORKSPACE_IDS)}, к удалению={planned}, не участники={not_member}, владельцев={owners}, ошибок пространств={ws_errors}")
        else:
            print(f"итого: пространств={len(WORKSPACE_IDS)}, удалено={removed}, не участники={not_member}, владельцев={owners}, ошибок пространств={ws_errors}")
        if missing:
            print(f"не найдено пользователей={len(missing)}: {', '.join(missing)}")
        if ws_errors or missing or owners:
            sys.exit(1)
    finally:
        await yggdrasil_connection.close()
        await heimdall_connection.close()
if __name__ == "__main__":
    asyncio.run(main())
