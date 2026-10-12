import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

from giga_finder.constants import *
from giga_finder.search import read_config
from giga_finder.ui import (
    _tag, action_label, animate_spinner, colorize, input_with_timeout,
    print_action, print_error, print_info, print_warn,
)
from giga_finder.zones import ZONES


def get_repo_root(start):
    if REPO_PATH.exists() and (REPO_PATH / ".git").exists():
        return REPO_PATH.resolve()
    for p in (start, start.parent, start.parent / "argocd"):
        if (p / ".git").exists():
            return p.resolve()
    cur = start.resolve()
    while cur != cur.parent:
        if (cur / ".git").exists():
            return cur
        cur = cur.parent
    raise FileNotFoundError("Не найден .git")


def _auto_restore_repo(repo_root):
    """Восстанавливает удалённые вручную файлы из последнего коммита; изменённые не трогает."""
    st = _git(repo_root, "status", "--porcelain")
    lines = [l for l in st.stdout.splitlines() if l.strip()]
    if not lines:
        return
    # Удалённые файлы: XY где Y='D' (working tree) или X='D' (staged delete)
    deleted_paths = []
    for line in lines:
        xy = line[:2]
        path = line[3:]
        if 'D' in xy:
            deleted_paths.append(path)
    modified = [l for l in lines if 'D' not in l[:2] and not l.startswith("??")]
    if deleted_paths:
        print_warn(f"Обнаружено {len(deleted_paths)} удалённых файла(ов) "
                   f"(возможно, содержимое переносили вручную).")
        answer = input_with_timeout("Восстановить их из последнего коммита? (yes/no): ", INPUT_TIMEOUT)
        if not (answer and answer.lower() in ("y", "yes")):
            print_info("Восстановление пропущено — поиск продолжится по текущему состоянию репозитория.")
            return
        # git restore только удалённые файлы, не трогая изменённые
        res = _git(repo_root, "restore", "--", *deleted_paths)
        if res.returncode != 0:
            # fallback: если restore по списку не сработал (много файлов) — через checkout
            res2 = _git(repo_root, "checkout", "--", ".")
            if res2.returncode != 0:
                print_error(f"Не удалось восстановить файлы: {res.stderr.strip()}")
                print_info("Выполните вручную: git restore . && git status")
                sys.exit(1)
        print_info("Удалённые файлы восстановлены из последнего коммита.")
    elif modified:
        print_warn(f"Есть локальные изменения ({len(modified)} файл(ов)) — они сохранены как есть, "
                   f"поиск использует актуальное содержимое.")

def _save_repo_path(target, quiet=False):
    REPO_LOCAL_FILE.write_text(str(target.resolve()), encoding="utf-8")
    if not quiet:
        print_info(f"Путь сохранён в {REPO_LOCAL_FILE.name} — в следующий раз спрашивать не буду.")

def _clone_repo(target):
    target.parent.mkdir(parents=True, exist_ok=True)
    res = subprocess.run(["git", "clone", REPO_URL, str(target)], capture_output=True, text=True)
    if res.returncode != 0:
        print_error(f"git clone failed: {res.stderr.strip()}")
        return False
    return True

def _repo_has_content(repo_root):
    """В корне репозитория есть хотя бы одна известная корневая папка зон."""
    return any((repo_root / d).exists() for d in ALLOWED_REPO_DIRS)

def _prompt_repo_root(script_dir):
    """Путь к репозиторию: сохранённый → ввод → клонирование. Результат запоминается."""
    # Попробуем ранее сохранённый путь
    if REPO_LOCAL_FILE.exists():
        saved = REPO_LOCAL_FILE.read_text(encoding="utf-8").strip()
        if saved and (Path(saved) / ".git").exists():
            print_info(f"Использую сохранённый путь к репозиторию: {saved}")
            return Path(saved).resolve()
        print_warn(f"Сохранённый путь недоступен ({saved}), укажу заново.")

    while True:
        raw = input_with_timeout(
            f"{action_label()} Укажите путь к локальному репозиторию argocd "
            f"(Enter — склонировать в {REPO_PATH}): ", INPUT_TIMEOUT
        )
        if raw is None:
            print_error("Таймаут ожидания. Запустите finder повторно.")
            sys.exit(1)
        target = Path(raw.strip()).expanduser() if raw.strip() else REPO_PATH
        if (target / ".git").exists():
            _save_repo_path(target)
            return target.resolve()
        answer = input_with_timeout(
            f"[WARN] В {target} нет .git. Склонировать туда {REPO_URL}? (y/n): ", INPUT_TIMEOUT
        )
        if answer is None:
            print_error("Таймаут ожидания. Запустите finder повторно.")
            sys.exit(1)
        if answer.strip().lower() in ("y", "yes"):
            try:
                if _clone_repo(target):
                    _save_repo_path(target, quiet=True)
                    print_info(f"Репозиторий склонирован в {target}, путь сохранён.")
                    return target.resolve()
                continue
            except FileNotFoundError:
                print_error("git не найден в системе. Установите git и запустите finder повторно.")
                sys.exit(1)
        else:
            print_warn("Введите существующий путь к репозиторию (с папкой .git).")

def _git(repo_root, *args):
    return subprocess.run(["git", *args], cwd=repo_root, capture_output=True, text=True)


def _stop_spinner(stop_event, spinner):
    stop_event.set()
    spinner.join()
    if sys.stdout.isatty():
        print()


def _extract_subdomain(config_path):
    """subdomain из config.yaml (для отчёта об изменениях)."""
    return read_config(config_path).get("cowork_subdomain") or "?"


def _report_changes(repo_root, news_dir, head_before, head_after):
    diff_stat = _git(repo_root, "diff", "--stat", head_before, head_after).stdout
    diff_full = _git(repo_root, "diff", head_before, head_after).stdout
    saved_path = None
    if diff_full:
        news_dir.mkdir(parents=True, exist_ok=True)
        now = datetime.now()
        fname = f"changes_{now.strftime('%Y%m%d_%H%M%S')}.txt"
        saved_path = news_dir / fname
        (saved_path).write_text(f"Изменения от {now}\n{'=' * 40}\n{diff_full}", encoding="utf-8")
    if diff_stat:
        files = _git(repo_root, "diff", "--name-only", head_before, head_after).stdout.strip().split("\n")
        print_info("Изменённые файлы (cowork_subdomain):")
        for f in files:
            if f:
                print(f"  {f} ({colorize(_extract_subdomain(repo_root / f), YELLOW)})")
    else:
        print_warn("Изменения затянуты, но статистика пуста.")
    if saved_path:
        print_info(f"Полные изменения сохранены в {saved_path}")


def pull_repo(repo_root, fatal=True):
    """git fetch + pull; возвращает (remote_url, head_before, head_after).

    fatal=False — ошибка не завершает программу, а возвращает None
    (используется фоновым обновлением из check_and_notify)."""
    stop_event = threading.Event()
    spinner = threading.Thread(target=animate_spinner, args=(stop_event,), daemon=True)
    spinner.start()
    suffix = " " if sys.stdout.isatty() else "..."
    print(f"{_tag('INFO', GREEN)} Обновление репозитория{suffix}", end='', flush=True)
    try:
        remote_url = _git(repo_root, "remote", "get-url", "origin").stdout.strip()
        head_before = _git(repo_root, "rev-parse", "HEAD").stdout.strip()
        fetch = _git(repo_root, "fetch", "origin")
        if fetch.returncode != 0:
            raise RuntimeError(f"git fetch failed: {fetch.stderr}")
        branch = _git(repo_root, "symbolic-ref", "--short", "HEAD").stdout.strip()
        if not branch:
            raise RuntimeError("Не удалось определить текущую ветку.")
        pull = _git(repo_root, "pull", "origin", branch)
        if pull.returncode != 0:
            raise RuntimeError(f"git pull failed: {pull.stderr}")
        head_after = _git(repo_root, "rev-parse", "HEAD").stdout.strip()
    except Exception as e:
        print_error(str(e))
        if not fatal:
            return None
        sys.exit(1)
    finally:
        _stop_spinner(stop_event, spinner)
    if remote_url:
        print_info(f"Удалённый репозиторий: {remote_url}")
    return remote_url, head_before, head_after


def report_pull(repo_root, news_dir, remote_url, head_before, head_after):
    """Отчёт о результате pull — вызывается после самопроверки зон."""
    if head_before and head_after and head_before != head_after:
        _report_changes(repo_root, news_dir, head_before, head_after)
    else:
        print_info("Новых изменений нет.")


# --- Фоновая проверка изменений в репозитории ---
class RepoChangeWatcher:
    """Фоновый наблюдатель за репозиторием argocd.

    Раз в REPO_CHECK_INTERVAL секунд выполняет тихий `git fetch origin` и
    сравнивает HEAD с origin/<ветка>. Если появились новые коммиты —
    выставляется флаг; уведомление показывает check_and_notify(), который
    вызывается ТОЛЬКО в момент простоя (перед экраном критериев поиска),
    чтобы не прерывать работу в меню. Пока накоплено больше
    REPO_FAST_CHECK_THRESHOLD коммитов и пользователь ещё не ответил,
    проверки идут чаще — каждые REPO_FAST_CHECK_INTERVAL секунд, чтобы
    счётчик изменений в уведомлении обновлялся без ожидания полного
    интервала. Ошибки сети/гита игнорируются — повторная попытка через
    следующий интервал."""

    def __init__(self, repo_root, interval=REPO_CHECK_INTERVAL):
        self.repo_root = Path(repo_root)
        self.interval = interval
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._changes_seen = False
        self._declined_remote = None  # состояние, за которое уже спрашивали и получили «no»
        self._remote_head = None      # текущее состояние origin/<ветка> после последнего fetch
        self._change_count = 0        # сколько коммитов на origin/ветке сверх локального HEAD
        self._thread = None

    def start(self):
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()

    def _run(self):
        while not self._stop_event.wait(self._current_interval()):
            try:
                self._check_once()
            except Exception:
                pass  # фоновая проверка: ошибки молча глотаем, повторим позже

    def _current_interval(self):
        """Интервал до следующей проверки.

        Если накопилось больше REPO_FAST_CHECK_THRESHOLD коммитов и мы ещё
        не спрашивали об этом состоянии — проверяем чаще (каждые
        REPO_FAST_CHECK_INTERVAL секунд), чтобы счётчик в уведомлении
        обновлялся без ожидания полного интервала."""
        with self._lock:
            if (self._changes_seen and self._declined_remote is None
                    and self._change_count > REPO_FAST_CHECK_THRESHOLD):
                return REPO_FAST_CHECK_INTERVAL
        return self.interval

    def _check_once(self):
        branch_res = _git(self.repo_root, "symbolic-ref", "--short", "HEAD")
        if branch_res.returncode != 0:
            return
        branch = branch_res.stdout.strip()
        if _git(self.repo_root, "fetch", "origin").returncode != 0:
            return
        local = _git(self.repo_root, "rev-parse", "HEAD")
        remote = _git(self.repo_root, "rev-parse", f"origin/{branch}")
        if local.returncode != 0 or remote.returncode != 0:
            return
        # Сколько коммитов на origin/ветке сверх локального HEAD (для уведомления).
        count_res = _git(self.repo_root, "rev-list", "--count", f"HEAD..origin/{branch}")
        try:
            count = int(count_res.stdout.strip()) if count_res.returncode == 0 else 0
        except ValueError:
            count = 0
        with self._lock:
            if remote.stdout.strip() != local.stdout.strip():
                # Новое состояние на удалёнке (или первое замеченное): спрашиваем.
                # Если это то же самое, за которое уже ответили «no» — не спамим.
                if remote.stdout.strip() != self._declined_remote:
                    self._changes_seen = True
                self._remote_head = remote.stdout.strip()
                self._change_count = count

    def pending_remote(self):
        """Состояние origin/<ветка>, за которое ещё не спрашивали (или None)."""
        with self._lock:
            if self._changes_seen and self._declined_remote is None:
                return self._remote_head
            return None

    def mark_declined(self, remote_head):
        """Пользователь ответил «no» — не спрашиваем снова об этом же состоянии."""
        with self._lock:
            self._declined_remote = remote_head
            if remote_head == self._remote_head:
                self._changes_seen = False

    def has_changes(self):
        with self._lock:
            return self._changes_seen

    def change_count(self):
        """Сколько новых коммитов на origin/ветке (0, если не удалось посчитать)."""
        with self._lock:
            return self._change_count

    def clear_changes(self):
        with self._lock:
            self._changes_seen = False
            self._declined_remote = None


WATCHER = None  # создаётся в main() после стартового pull


def check_and_notify(repo_root, news_dir, zones):
    """Уведомление о новых изменениях в репозитории.

    Вызывать только в простое — перед экраном критериев поиска. При
    подтверждении выполняет pull, пересобирает зоны (in-place, чтобы
    ссылки в main остались валидными) и печатает отчёт об изменениях."""
    if WATCHER is None or not WATCHER.has_changes():
        return False
    # Фиксируем состояние удалёнки ДО вопроса: ответ «no» относится именно к нему.
    remote_head = WATCHER.pending_remote()
    count = WATCHER.change_count()
    if count > 0:
        # Скобки — белые, только цифра жёлтая (вне TTY без ANSI-кодов).
        if sys.stdout.isatty():
            counter = f"{WHITE}({YELLOW}{count}{RESET}){RESET}"
        else:
            counter = f"({count})"
        print_warn(f"Найдены изменения в конфигурациях стендов {counter}.")
    else:
        print_warn("Найдены изменения в конфигурациях стендов.")
    answer = input_with_timeout("Обновить локальный репозиторий? (yes/no): ", INPUT_TIMEOUT)
    if not (answer and answer.lower() in ("y", "yes")):
        # «no» — не дёргаем за то же состояние удалёнки снова, пока не появится новый коммит.
        WATCHER.mark_declined(remote_head)
        return False
    pull_result = pull_repo(repo_root, fatal=False)
    if pull_result is None:
        WATCHER.mark_declined(remote_head)
        return False
    zones[:] = ZONES.init(repo_root)
    ZONES.verify(repo_root, zones)
    report_pull(repo_root, news_dir, *pull_result)
    WATCHER.clear_changes()
    return True

def _init_environment(script_dir):
    """Общая инициализация (репозиторий, зоны, pull, watcher) для обоих режимов запуска."""
    global WATCHER
    try:
        repo_root = get_repo_root(script_dir)
    except FileNotFoundError as e:
        print_error(str(e))
        print_info("Попробуем найти репозиторий вручную.")
        repo_root = _prompt_repo_root(script_dir)
    while not _repo_has_content(repo_root):
        print_error(f"Папка {repo_root} не содержит данных репозитория "
                    f"(ожидаются папки: {', '.join(ALLOWED_REPO_DIRS)}).")
        print_info("Возможные причины: содержимое папки перенесли в другое место, "
                   "клонирование прервалось или это не та папка.")
        print_action("Что делать?")
        print("  1. Указать другой путь к репозиторию")
        print("  2. Склонировать репозиторий в эту папку")
        print("  3. Выйти")
        choice = input_with_timeout("Выберите вариант (1-3): ", INPUT_TIMEOUT)
        if choice is None or choice.strip() == "3":
            print_warn("Завершение работы.")
            sys.exit(1)
        if choice.strip() == "1":
            repo_root = _prompt_repo_root(script_dir)
            continue
        if choice.strip() == "2":
            if not _clone_repo(repo_root):
                print_info("Если папка не пустая — очистите её вручную и повторите попытку.")
                continue
            _save_repo_path(repo_root, quiet=True)
            print_info(f"Репозиторий склонирован в {repo_root}, путь сохранён.")
            break
        print_warn("Неверный ввод, попробуйте снова.")

    _auto_restore_repo(repo_root)
    pull_result = pull_repo(repo_root)
    zones = ZONES.init(repo_root)
    ZONES.verify(repo_root, zones)
    report_pull(repo_root, script_dir / "news", *pull_result)
    WATCHER = RepoChangeWatcher(repo_root)
    WATCHER.start()
    return repo_root, zones
