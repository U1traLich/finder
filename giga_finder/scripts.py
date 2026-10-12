import json

# --- Управление пространствами: шаблоны сгенерированных скриптов ---
# Полные шаблоны (тело — рабочие версии change_workspace_owner / add_all_users_to_workspace), значения подставляются
# через .replace(), чтобы не экранировать фигурные скобки во внутренних f-строках.

_SCRIPT_COMMON_HELPERS = '''# ===== Общие хелперы встроенных скриптов (импорты + DSN + запросы) =====
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
'''
_CHANGE_OWNER_TEMPLATE = '''

OWNER_ROLE_ID = "workspace.owner"
MEMBER_ROLE_ID = "workspace.member"

WORKSPACE_IDS = [
__IDS__
]
NEW_OWNER_EMAIL = __EMAIL__
# True — только показать, что будет сделано, ничего не менять
DRY_RUN = __DRY__

DEFAULT_YGGDRASIL_URL = __YGG__
DEFAULT_HEIMDALL_URL = __HM__

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
'''

_MASS_ADD_TEMPLATE = '''

MEMBER_ROLE_ID = "workspace.member"
WORKSPACE_IDS = [
__IDS__
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
                                   __YGG__)
    heimdall_url = os.environ.get("HEIMDALL_DATABASE_URL",
                                  __HM__)

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
'''

_ADD_USER_TEMPLATE = '''from datetime import datetime, timezone

MEMBER_ROLE_ID = "workspace.member"

WORKSPACE_IDS = [
__IDS__
]
USER_EMAILS = [
__EMAILS__
]
# True — только показать, что будет сделано, ничего не менять
DRY_RUN = __DRY__

DEFAULT_YGGDRASIL_URL = __YGG__
DEFAULT_HEIMDALL_URL = __HM__

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
'''

_REMOVE_USER_TEMPLATE = '''WORKSPACE_IDS = [
__IDS__
]
USER_EMAILS = [
__EMAILS__
]
# Дополнительные user_id (UUID), которые нужно проверить/удалить.
# Заполните, если пользователь не находится по email (осиротевшие гранты).
EXTRA_USER_IDS = [
__EXTRA_IDS__
]
# True — только показать, что будет сделано, ничего не менять
DRY_RUN = __DRY__
DEFAULT_YGGDRASIL_URL = __YGG__
DEFAULT_HEIMDALL_URL = __HM__
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
'''

_LIST_WORKSPACES_TEMPLATE = '''

async def main() -> None:
    yggdrasil_url = get_database_url(("YGGDRASIL_DATABASE_URL",), __YGG__)

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
'''


def _json_list_block(values):
    """Блок JSON-значений для вставки в список Python: по одному на строку, отступ 4."""
    return ",\n".join(f"    {json.dumps(v)}" for v in values)


def _render_script(template, **replacements):
    """Собирает скрипт: общий блок хелперов + шаблон + подстановка плейсхолдеров __KEY__."""
    result = _SCRIPT_COMMON_HELPERS + template
    for key, value in replacements.items():
        result = result.replace(f"__{key}__", value)
    return result


def generate_change_owner_script(workspace_ids, new_owner_email, dry_run, ygg_url, hm_url):
    return _render_script(_CHANGE_OWNER_TEMPLATE,
                          IDS=_json_list_block(workspace_ids),
                          EMAIL=json.dumps(new_owner_email),
                          DRY="True" if dry_run else "False",
                          YGG=json.dumps(ygg_url),
                          HM=json.dumps(hm_url))


def generate_mass_add_script(workspace_ids, ygg_url, hm_url):
    return _render_script(_MASS_ADD_TEMPLATE,
                          IDS=_json_list_block(workspace_ids),
                          YGG=json.dumps(ygg_url),
                          HM=json.dumps(hm_url))


def generate_add_user_script(workspace_ids, user_emails, dry_run, ygg_url, hm_url):
    return _render_script(_ADD_USER_TEMPLATE,
                          IDS=_json_list_block(workspace_ids),
                          EMAILS=_json_list_block(user_emails),
                          DRY="True" if dry_run else "False",
                          YGG=json.dumps(ygg_url),
                          HM=json.dumps(hm_url))


def generate_remove_user_script(workspace_ids, user_emails, dry_run, ygg_url, hm_url, extra_user_ids=None):
    extra_ids = extra_user_ids or []
    extra_block = _json_list_block(extra_ids) if extra_ids else "    # (пусто — не нужно)"
    return _render_script(_REMOVE_USER_TEMPLATE,
                          EXTRA_IDS=extra_block,
                          IDS=_json_list_block(workspace_ids),
                          EMAILS=_json_list_block(user_emails),
                          DRY="True" if dry_run else "False",
                          YGG=json.dumps(ygg_url),
                          HM=json.dumps(hm_url))


def generate_list_workspaces_script(ygg_url):
    return _render_script(_LIST_WORKSPACES_TEMPLATE, YGG=json.dumps(ygg_url))

# --- Починить роли на стенде (heimdall_auth) ---
_FIX_EMPTY_ROLES_TEMPLATE = '''# Роль «Участник» (global role id в heimdall_auth)
MEMBER_ROLE_ID = "6441ce25-27e5-436a-ae2d-374d080dcab1"
# Исключения: тестовый аккаунт и внешние пользователи не трогаем
EXCLUDE_EMAILS = ["cp-test@gigab2b.ru"]
EXTERNAL_PREFIX = "ext-"
# True — только показать, кто без ролей, ничего не менять
DRY_RUN = __DRY__
DEFAULT_HEIMDALL_URL = __HM__
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
'''
def generate_fix_empty_roles_script(dry_run, hm_url):
    return _render_script(_FIX_EMPTY_ROLES_TEMPLATE,
                          DRY="True" if dry_run else "False",
                          HM=json.dumps(hm_url))


_RESET_STALE_SESSIONS_TEMPLATE = '''from datetime import datetime, timezone

# True — только показать, какие сессии будут сброшены, ничего не менять
DRY_RUN = __DRY__
# Порог staleness в минутах: сессия считается зависшей, если last_active_at старше этого значения.
STALE_MINUTES = 5
# Конкретный session_id для точечного сброса (None — массовый режим по порогу).
SESSION_ID = __SESSION_ID__
DEFAULT_YGGDRASIL_URL = __YGG__

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
            print(f"    застряла: ~{stuck_for:.1f} мин")
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
                print(f"[OK] session_id={row['id']} сброшена (застряла ~{stuck_for:.1f} мин)")
            else:
                print(f"[SKIP] session_id={row['id']}: статус уже изменился или есть interrupt_data")
        print()
        print(f"итого: сброшено={updated}, пропущено={len(stale) - updated}")
    finally:
        await connection.close()

if __name__ == "__main__":
    asyncio.run(main())
'''


def generate_reset_stale_sessions_script(dry_run, ygg_url, session_id=None):
    sid = json.dumps(session_id) if session_id else "None"
    return _render_script(_RESET_STALE_SESSIONS_TEMPLATE,
                          DRY="True" if dry_run else "False",
                          SESSION_ID=sid,
                          YGG=json.dumps(ygg_url))

_CREATE_ACCOUNTS_TEMPLATE = '''import asyncio, secrets, string, sys, httpx
try:
    from auth_sdk import AuthClient, KeycloakProviderConfig
except ModuleNotFoundError:
    from heimdall_auth import AuthClient, KeycloakProviderConfig
try:
    from auth_service.bootstrap.config import get_settings
except ModuleNotFoundError:
    from auth_service.config import get_settings

_ESC = chr(27)
GREEN, RED, YELLOW, RESET = f"{_ESC}[32m", f"{_ESC}[31m", f"{_ESC}[33m", f"{_ESC}[0m"

# Логин = часть до @; email собирается как логин@домен стенда
USERS = [
__USERS__
]
DOMAIN = __DOMAIN__

def make_password(length=24):
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*()-_=+"
    while True:
        password = "".join(secrets.choice(alphabet) for _ in range(length))
        if any(c.islower() for c in password) and any(c.isupper() for c in password) and any(c.isdigit() for c in password) and any(c in "!@#$%^&*()-_=+" for c in password):
            return password

async def main():
    if not DOMAIN or "@" in DOMAIN:
        print(f"{RED}ERROR:{RESET} заполните корректный домен")
        sys.exit(2)
    if not USERS:
        print(f"{RED}ERROR:{RESET} список пользователей пуст")
        sys.exit(2)
    settings = get_settings()
    async with httpx.AsyncClient() as http:
        auth_client = AuthClient(
            config=KeycloakProviderConfig(
                issuer_url=settings.provider_issuer_url,
                realm=settings.provider_realm,
                client_id=settings.provider_client_id,
                client_secret=settings.provider_client_secret,
            ),
            http_client=http,
        )
        created_count = 0
        reused_count = 0
        for login in USERS:
            email = f"{login}@{DOMAIN}"
            fio = login
            parts = fio.split()
            last_name = parts[0]
            first_name = " ".join(parts[1:]) if len(parts) > 1 else "Technical"
            user = await auth_client.find_user_by_email(email=email)
            if user is None:
                user = await auth_client.create_provider_user(email=email, first_name=first_name, last_name=last_name)
                created = True
            else:
                created = False
            password = make_password()
            await auth_client.reset_user_password(user_id=user.id, new_password=password)
            action = "создан новый пользователь" if created else "использован существующий пользователь"
            print(f"{GREEN}[OK]{RESET} {action}")
            print("")
            print(f"  email:    {email}")
            print(f"  password: {password}")
            print(f"  user_id:  {user.id}")
            if created:
                created_count += 1
            else:
                reused_count += 1
        print("")
        print(f"ИТОГО: создано новых: {created_count}, использовано существующих: {reused_count}")

if __name__ == "__main__":
    asyncio.run(main())
'''


def generate_create_accounts_script(logins, domain):
    """Команда heredoc для пода heimdall-auth: создание/сброс паролей нескольких аккаунтов."""
    body = (_CREATE_ACCOUNTS_TEMPLATE
            .replace("__USERS__", _json_list_block(logins))
            .replace("__DOMAIN__", json.dumps(domain)))
    return f"/app/services/auth/.venv/bin/python <<'PY'\n{body}PY\n"
