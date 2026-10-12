import re
from pathlib import Path

from giga_finder.constants import *
from giga_finder.ui import colorize, print_action, print_warn

_CONFIG_KEYS = ("stand_name", "cowork_subdomain", "admin_email", "expiry_date", "cowork_revision", "public_hostname", "status")


def read_config(file_path):
    try:
        content = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}
    data = {}
    for key in _CONFIG_KEYS:
        m = re.search(rf'{key}:\s*"?([^"\n]+)"?', content)
        data[key] = m.group(1).strip() if m else None
    m = re.search(r'(?:meta\.)?inn:\s*"?([^"\n]+)"?', content)
    data["inn"] = m.group(1).strip() if m else None
    data["broken"] = "# broken stand" in content
    return data


def _matches(data, criteria):
    for criterion, value in (
        (criteria.get("subdomain"), data.get("cowork_subdomain")),
        (criteria.get("email"), data.get("admin_email")),
        (criteria.get("stand_name"), data.get("stand_name")),
    ):
        if criterion and value and criterion.lower() in value.lower():
            return True
    inn = criteria.get("inn")
    return bool(inn and data.get("inn") and inn in data["inn"])


def _is_empty_stand(data):
    """Пустой (незаполненный) стенд: нет ни admin_email, ни ИНН, ни expiry_date."""
    return not (data.get("admin_email") or data.get("inn") or data.get("expiry_date"))


def _exact_match(data, criteria, folder):
    """Точное совпадение критерия поиска с именем папки/стенда, subdomain, email или ИНН.

    Если пользователь ввёл значение целиком (а не подстроку) — стенд показываем
    даже при фильтрации пустых/дефолтных."""
    folder_l = folder.strip().lower()
    for key, value in (
        ("stand_name", data.get("stand_name")),
        ("subdomain", data.get("cowork_subdomain")),
    ):
        crit = (criteria.get(key) or "").strip().lower()
        val = (value or "").strip().lower()
        if crit and (crit == val or crit == folder_l):
            return True
    email_crit = (criteria.get("email") or "").strip().lower()
    email_val = (data.get("admin_email") or "").strip().lower()
    if email_crit and email_crit == email_val:
        return True
    inn_crit = (criteria.get("inn") or "").strip()
    if inn_crit and data.get("inn") and inn_crit in data["inn"]:
        return True
    return False


def iter_stand_configs(root, zones):
    """Все config.yaml стендов: (zone, path, data). Единственный обход репозитория."""
    root = Path(root)
    for zone in zones:
        base = root / zone["path"]
        if not base.exists():
            continue
        prefixes = tuple(zone.get("stand_prefixes", ("cowork-",)))
        for config_path in base.rglob(CONFIG):
            if not config_path.parent.name.startswith(prefixes):
                continue
            yield zone, config_path, read_config(config_path)


def load_stand_index(root, zones):
    """Снимок конфигов. После pull его нужно собрать заново."""
    return list(iter_stand_configs(root, zones))


def find_stands(root, zones, criteria, rows=None):
    results = []
    source = rows if rows is not None else iter_stand_configs(root, zones)
    for zone, config_path, raw in source:
        data = dict(raw)
        stand_dir = config_path.parent
        # Точное совпадение по имени — показываем даже «пустой»/дефолтный стенд.
        exact = _exact_match(data, criteria, stand_dir.name)
        # Показываем, если нашлось совпадение по полям конфига ИЛИ точное
        # совпадение критерия с именем папки/стенда (конфиг может быть неполным).
        if not _matches(data, criteria) and not exact:
            continue
        # Пустые стенды (без admin_email/ИНН/expiry_date) в выдачу не попадаем.
        if not exact and _is_empty_stand(data):
            continue
        # Дефолтный subdomain (совпадает с именем папки) — стенд незанятый, тоже не показываем.
        sub = (data.get("cowork_subdomain") or "").strip()
        if not exact and sub and sub == stand_dir.name:
            continue
        data["folder"] = stand_dir.name
        data["stand_dir"] = zone["label"]
        data["zone_path"] = zone["path"]
        data["url_template"] = zone["url"]
        data["config_path"] = config_path
        results.append(data)
    return results


# Подсветка status стенда: trial — жёлтый, grace — голубой, expired — красный, active — зелёный; неизвестные — фиолетовые.
_STATUS_COLORS = {
    "trial": YELLOW,
    "grace": CYAN,
    "expired": RED,
    "active": GREEN,
}


def _status_color(status):
    return _STATUS_COLORS.get((status or "").strip().lower(), MAGENTA)


def _print_stand(idx, data):
    print(f"\n{idx}. Папка: {data.get('folder', 'N/A')}")
    print(f"   Расположение: {data.get('stand_dir', 'N/A')}")
    print(f"   stand_name: {data.get('stand_name', 'N/A')}")
    print(f"   cowork_subdomain: {data.get('cowork_subdomain', 'N/A')}")
    if data.get('public_hostname'):
        print(f"   public_hostname: {data.get('public_hostname')}")
    print(f"   admin_email: {data.get('admin_email', 'N/A')}")
    print(f"   ИНН: {data.get('inn', 'N/A')}")
    print(f"   expiry_date: {data.get('expiry_date', 'N/A')}")
    if data.get('status'):
        print(f"   status: {colorize(data['status'], _status_color(data['status']))}")
    print(f"   cowork_revision: {data.get('cowork_revision', 'N/A')}")
    if data.get("broken"):
        print(f"   {colorize('[broken stand]', RED)}")


def select_stand(results):
    if len(results) == 1:
        return results[0]
    print_warn("Найдено несколько стендов.")
    for i, data in enumerate(results, 1):
        sub = data.get('cowork_subdomain', 'N/A')
        print(f"  {i}. {data['folder']} ({colorize(sub, YELLOW)})")
    while True:
        print_action("Введите номер стенда (0 для отмены):", end="")
        choice = input().strip()
        if choice == '0':
            return None
        if choice.isdigit() and 1 <= int(choice) <= len(results):
            return results[int(choice) - 1]
        print_warn("Неверный ввод, попробуйте снова.")
