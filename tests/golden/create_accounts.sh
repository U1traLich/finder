/app/services/auth/.venv/bin/python <<'PY'
import asyncio, secrets, string, sys, httpx
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
    "user81"
]
DOMAIN = "21-school.ru"

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
PY
