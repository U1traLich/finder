/app/services/auth/.venv/bin/python <<'PY'
import asyncio, sys, httpx
from datetime import datetime, timezone
try:
    from auth_service.bootstrap.config import get_settings
except ModuleNotFoundError:
    from auth_service.config import get_settings

async def main():
    settings = get_settings()
    base_url = settings.provider_issuer_url.split("/realms/")[0]
    realm = settings.provider_realm

    async with httpx.AsyncClient() as http:
        token_resp = await http.post(
            f"{base_url}/realms/{realm}/protocol/openid-connect/token",
            data={
                "grant_type": "client_credentials",
                "client_id": settings.provider_client_id,
                "client_secret": settings.provider_client_secret,
            },
        )
        token_resp.raise_for_status()
        access_token = token_resp.json()["access_token"]

        all_users = []
        first = 0
        page_size = 100
        while True:
            resp = await http.get(
                f"{base_url}/admin/realms/{realm}/users",
                params={"first": first, "max": page_size},
                headers={"Authorization": f"Bearer {access_token}"},
            )
            resp.raise_for_status()
            users = resp.json()
            if not users:
                break
            all_users.extend(users)
            if len(users) < page_size:
                break
            first += page_size

    all_users.sort(key=lambda u: u.get("email", "").lower())
    print(f"Total: {len(all_users)}")
    print()
    for u in all_users:
        email = u.get("email", "—")
        name = f"{u.get('firstName', '')} {u.get('lastName', '')}".strip() or "—"
        uid = u.get("id", "—")
        status = "active" if u.get("enabled") else "disabled"
        ts = u.get("createdTimestamp")
        created = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d") if ts else "—"
        print(f"{email}  |  {name}  |  {status}  |  created={created}  |  id={uid}")

asyncio.run(main())
PY