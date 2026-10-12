import re
import zipfile
from datetime import datetime
from pathlib import Path

from giga_finder.argocd import open_pod_exec
from giga_finder.constants import *
from giga_finder.dsn import _stand_name
from giga_finder.scripts import generate_create_accounts_script
from giga_finder.ui import action_label, input_with_timeout, is_help_request, print_error, print_hint, print_info, print_warn

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def parse_accounts_output(text):
    """Разбор вывода create_accounts: пары (email, password) в порядке появления."""
    text = _ANSI_RE.sub("", text or "")
    accounts = []
    current_email = None
    for line in text.splitlines():
        m = re.match(r"^\s*email:\s*(\S+@\S+)\s*$", line)
        if m:
            current_email = m.group(1)
            continue
        m = re.match(r"^\s*password:\s*(\S+)\s*$", line)
        if m and current_email:
            accounts.append((current_email, m.group(1)))
            current_email = None
    return accounts


def _write_accounts_xlsx(path, accounts):
    """Минимальный .xlsx (zip + XML, только stdlib): заголовки email/password + строки."""
    def esc(value):
        return (str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                .replace('"', "&quot;"))
    rows_xml = ['<row r="1"><c r="A1" t="inlineStr"><is><t>email</t></is></c>'
                '<c r="B1" t="inlineStr"><is><t>password</t></is></c></row>']
    for i, (email, password) in enumerate(accounts, start=2):
        rows_xml.append(
            f'<row r="{i}">'
            f'<c r="A{i}" t="inlineStr"><is><t>{esc(email)}</t></is></c>'
            f'<c r="B{i}" t="inlineStr"><is><t>{esc(password)}</t></is></c>'
            f'</row>'
        )
    sheet = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
             f'<sheetData>{"".join(rows_xml)}</sheetData></worksheet>')
    content_types = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                     '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                     '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                     '<Default Extension="xml" ContentType="application/xml"/>'
                     '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                     '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                     '</Types>')
    root_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                 '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                 '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
                 '</Relationships>')
    workbook = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                '<sheets><sheet name="accounts" sheetId="1" r:id="rId1"/></sheets></workbook>')
    workbook_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                     '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                     '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
                     '</Relationships>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", root_rels)
        zf.writestr("xl/workbook.xml", workbook)
        zf.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        zf.writestr("xl/worksheets/sheet1.xml", sheet)
    return path


def _accounts_domain(selected):
    """Домен для email аккаунтов: из admin_email или из cowork_subdomain."""
    admin_email = (selected.get("admin_email") or "").strip()
    if "@" in admin_email:
        return admin_email.rsplit("@", 1)[1].strip().lower()
    subdomain = (selected.get("cowork_subdomain") or "").strip().lower()
    return subdomain or None


def create_stand_accounts(selected, zone):
    """Пункт 9: создать несколько аккаунтов на стенде → Excel с email/password."""
    print_info("Введите логины через запятую (логин — часть до @; "
               "FIO будет подставлен из логина автоматически).")
    print_hint()
    while True:
        raw = input(f"{action_label()} Логины: ").strip()
        if is_help_request(raw):
            from giga_finder.helptext import print_accounts_help
            print_accounts_help()
            continue
        break
    logins = []
    for token in re.split(r"[,\s]+", raw):
        token = token.strip()
        if not token:
            continue
        if "@" in token:
            token = token.split("@", 1)[0]
        logins.append(token)
    if not logins or any(not re.fullmatch(r"[A-Za-z0-9._-]+", l) for l in logins):
        print_warn("Некорректные логины (допустимы буквы, цифры, точка, дефис, underscore), операция отменена.")
        return
    if len(set(logins)) != len(logins):
        print_warn("Есть повторяющиеся логины, операция отменена.")
        return
    domain = _accounts_domain(selected)
    print_info(f"Домен для email: {domain or 'не определён'}")
    override = input(f"{action_label()} Enter — принять, либо введите свой домен: ").strip().lower()
    if override:
        domain = override
    if not domain or "@" in domain or " " in domain:
        print_warn("Некорректный домен, операция отменена.")
        return
    open_pod_exec._opened = False
    print_info("Открываю приложение heimdall в ArgoCD — скопируйте скрипт в веб-терминал.")
    open_pod_exec(zone["code"], _stand_name(selected), "heimdall", zone.get("app_template"), zone.get("argocd_base"))
    script = generate_create_accounts_script(logins, domain)
    out = EXEC_DIR / f"create_accounts_{selected.get('folder') or 'stand'}.sh"
    EXEC_DIR.mkdir(parents=True, exist_ok=True)
    out.write_text(script, encoding="utf-8")
    print_info(f"Скрипт сохранён: {out}")
    print_info("В списке приложения выберите под heimdall-auth* (вкладка exec), "
               f"скопируйте содержимое {out.name} целиком в веб-терминал и запустите.")
    paste = input_with_timeout(f"{action_label()} Вставьте вывод скрипта целиком (Enter без текста — отмена): ", INPUT_TIMEOUT)
    if paste is None:
        print_warn("Операция отменена.")
        return
    accounts = parse_accounts_output(paste)
    if not accounts:
        print_warn("Не удалось распознать ни одной пары email/password в выводе, операция отменена.")
        return
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    # Папка accounts/ рядом с finder-pro.py: создаём только если её ещё нет
    if not ACCOUNTS_DIR.exists():
        ACCOUNTS_DIR.mkdir(parents=True)
    xlsx_path = ACCOUNTS_DIR / f"accounts_{selected.get('folder') or 'stand'}_{stamp}.xlsx"
    try:
        _write_accounts_xlsx(xlsx_path, accounts)
    except OSError as error:
        print_error(f"Не удалось сохранить Excel: {error}")
        return
    print_info(f"Готово: {len(accounts)} аккаунтов → {xlsx_path}")
