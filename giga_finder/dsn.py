import json
import re
from datetime import datetime
from pathlib import Path

from giga_finder.argocd import open_pod_exec
from giga_finder.constants import *
from giga_finder.ui import action_label, colorize, is_help_request, print_info, print_warn


def _ask(prompt):
    """input(), а на ? — подсказка по строке DSN и повтор вопроса."""
    while True:
        value = input(prompt)
        if is_help_request(value):
            from giga_finder.helptext import print_dsn_help
            print_dsn_help()
            continue
        return value

def build_db_url(user, host, folder, db):
    # Шаблон с полным DNS-суффиксом неймспейса стенда (неймспейс = имя папки стенда).
    return f"postgresql://{user}:<ПАРОЛЬ>@{host}.{folder}.svc.cluster.local:5432/{db}"

def _extract_dsn(raw):
    """DSN из строки 'ИМЯ_ПЕРЕМЕННОЙ=postgresql://...'; чистый DSN принимается как есть."""
    value = raw.strip()
    if not value:
        return None
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):  # скопировано с кавычками
        value = value[1:-1].strip()
    if not value.startswith(("postgresql://", "postgres://")) and "=" in value:  # префикс ИМЯ_ПЕРЕМЕННОЙ=
        value = value.split("=", 1)[1].strip()
    if not (value.startswith("postgresql://") or value.startswith("postgres://")):
        return None
    return value


def _expand_dns_suffix(url, folder):
    """Короткому хосту DSN дополняется суффикс <stand_name>.svc.cluster.local (под отдаёт имя сервиса без неймспейса)."""
    if not folder:
        return url
    m = re.match(r"^(postgresql?://[^@/]+@)([^:/]+)(:\d+)?(/.*)$", url)
    if not m:
        return url
    scheme_user, host, port, path = m.groups()
    if ".svc.cluster.local" in host:
        return url
    new_host = f"{host}.{folder}.svc.cluster.local"
    return f"{scheme_user}{new_host}{port or ''}{path}"


def _resolve_db_url(raw, user, host, db, folder, var_name):
    """Разбирает введённую строку в DSN; при неудаче — шаблон с <ПАРОЛЬ>."""
    url = _extract_dsn(raw)
    if raw and url is None:
        print_warn("Строка не распознана (ожидается 'ИМЯ=postgresql://...'). Вставлен шаблон с <ПАРОЛЬ>.")
    if url is None:
        url = build_db_url(user, host, folder, db)
        if not raw:
            print_warn(f"{var_name} не задан — вставлен шаблон с <ПАРОЛЬ>, заполните в скрипте.")
    else:
        url = _expand_dns_suffix(url, folder)
    return url


class DsnCache:
    """Кэш DSN по стендам (файл ~/.gigafinder/dsn-cache.json).

    Общий фоллбек всех операций: «кэш → ArgoCD → ввод» и сохранение
    распознанного DSN обратно в кэш.
    """

    def __init__(self, path):
        self.path = Path(path)
        self.data = {}
        try:
            with open(self.path, encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                self.data = loaded
        except (OSError, json.JSONDecodeError):
            pass

    def save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except OSError as error:
            print_warn(f"Не удалось сохранить кэш DSN: {error}")

    def entry(self, folder):
        return self.data.get(folder) or {}

    def offer_cached(self, folder, key, label):
        """Предложить значение из кэша; True — использовать, False — ввести заново."""
        value = self.entry(folder).get(key)
        if not value:
            return False
        saved_at = self.entry(folder).get("saved_at", "дата неизвестна")
        print_info(f"Найден кэш DSN {label} для стенда {folder} (сохранён {saved_at}).")
        choice = _ask(f"{action_label()} Enter — использовать из кэша, d — ввести заново: ").strip().lower()
        if choice != "d":
            print_info(f"Использую DSN {label} из кэша для стенда {folder}.")
            return True
        return False

    def store(self, folder, note, **values):
        """Сохранить значения в кэш (только распознанные DSN) и показать подсказку."""
        if any("<ПАРОЛЬ>" in v for v in values.values()):
            return
        entry = dict(self.entry(folder))
        entry.update(values)
        entry["saved_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        self.data[folder] = entry
        self.save()
        print_info(f"{note} в кэш ({self.path}) — в следующий раз вводить не придётся.")


def _stand_name(selected):
    """Имя стенда для отображения: stand_name или folder как фоллбек."""
    return selected.get("stand_name") or selected.get("folder", "")


def prompt_db_urls(selected, zone, need_heimdall=True):
    folder = selected.get("folder", "")
    stand_name = _stand_name(selected)

    cache = DSN_CACHE
    cached = cache.entry(folder)
    cached_ygg = cached.get("yggdrasil")
    cached_hm = cached.get("heimdall")
    if cached_ygg and (not need_heimdall or cached_hm):
        saved_at = cached.get("saved_at", "дата неизвестна")
        print_info(f"Найден кэш DSN для стенда {folder} (сохранён {saved_at}).")
        choice = _ask(f"{action_label()} Enter — использовать из кэша, d — ввести заново: ").strip().lower()
        if choice != "d":
            ygg = cached_ygg
            hm = cached_hm if need_heimdall else None
            print_info(f"Использую DSN из кэша для стенда {folder}.")
            return ygg, hm

    open_pod_exec._opened = False
    print_info("Открываю приложение yggdrasil в ArgoCD — скопируйте YGGDRASIL_DATABASE_URL.")
    open_pod_exec(zone["code"], stand_name, "yggdrasil", zone.get("app_template"), zone.get("argocd_base"))
    ygg_raw = _ask(f"{action_label()} Вставьте строку в под:\n{colorize('env | grep YGGDRASIL_DATABASE_URL', CYAN)}\nвывод вставьте сюда: ").strip()

    hm_raw = ""
    if need_heimdall:
        print_info("Открываю приложение heimdall в ArgoCD — скопируйте AUTH_DATABASE_URL.")
        open_pod_exec(zone["code"], stand_name, "heimdall", zone.get("app_template"), zone.get("argocd_base"))
        hm_raw = _ask(f"{action_label()} Вставьте строку в под:\n{colorize('env | grep AUTH_DATABASE_URL', CYAN)}\nвывод вставьте сюда: ").strip()

    ygg = _resolve_db_url(ygg_raw, "yggdrasil", "yggdrasil-postgres-rw", "yggdrasil", folder, "YGGDRASIL_DATABASE_URL")
    hm = None
    if need_heimdall:
        hm = _resolve_db_url(hm_raw, "heimdall_auth", "heimdall-auth-postgres-rw", "heimdall_auth", folder, "HEIMDALL_DATABASE_URL")

    if "<ПАРОЛЬ>" not in ygg and "<ПАРОЛЬ>" not in (hm or ""):  # в кэш — только распознанные DSN
        values = {"yggdrasil": ygg}
        if need_heimdall:
            values["heimdall"] = hm
        cache.store(folder, "DSN сохранены", **values)

    return ygg, hm


def prompt_heimdall_url(selected, zone):
    """Запрашивает DSN heimdall_auth (кэш → ArgoCD → ввод); возвращает URL."""
    folder = selected.get("folder", "")
    stand_name = _stand_name(selected)
    cache = DSN_CACHE
    if cache.offer_cached(folder, "heimdall", "heimdall"):
        return cache.entry(folder)["heimdall"]
    open_pod_exec._opened = False
    print_info("Открываю приложение heimdall в ArgoCD — скопируйте AUTH_DATABASE_URL.")
    open_pod_exec(zone["code"], stand_name, "heimdall", zone.get("app_template"), zone.get("argocd_base"))
    hm_raw = _ask(f"{action_label()} Вставьте строку в под:\n{colorize('env | grep AUTH_DATABASE_URL', CYAN)}\nвывод вставьте сюда: ").strip()
    hm = _resolve_db_url(hm_raw, "heimdall_auth", "heimdall-auth-postgres-rw", "heimdall_auth", folder, "HEIMDALL_DATABASE_URL")
    if "<ПАРОЛЬ>" not in hm:
        cache.store(folder, "DSN heimdall сохранён", heimdall=hm)
    return hm


DSN_CACHE = DsnCache(DSN_CACHE_PATH)
