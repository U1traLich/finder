import json
import sys
from pathlib import Path

from giga_finder.constants import *
from giga_finder.ui import _tag, input_with_timeout, print_error, print_info, print_warn

def _zone_path_allowed(path):
    """Проверка, что путь зоны лежит внутри одной из ALLOWED_REPO_DIRS."""
    top = path.split("/", 1)[0]
    return top in ALLOWED_REPO_DIRS

class ZoneManager:
    """Зоны Finder: конфигурация zones_config.json + синхронизация с репозиторием."""

    def __init__(self, config_file):
        self.config_file = Path(config_file)


    def load_zones(self):
        if not self.config_file.exists():
            return []
        try:
            zones = json.loads(self.config_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        if not isinstance(zones, list):
            return []
        # Защита: из конфига берём только зоны в допустимых корневых папках.
        zones = [z for z in zones if isinstance(z, dict) and _zone_path_allowed(z.get("path", ""))]
        # JSON хранит кортежи как списки — приводим stand_prefixes к tuple.
        for zone in zones:
            if "stand_prefixes" in zone:
                zone["stand_prefixes"] = tuple(zone["stand_prefixes"])
        # Автокоррекция URL, если ранее был создан файл с багом.
        corrected = False
        for zone in zones:
            if "url" in zone and "argocd-cce-prod-" in zone["url"]:
                code = zone["path"].split("/")[-1].rsplit("-", 1)[-1]
                zone["url"] = f"https://argocd-{code}.cowork.ru/applications/cowork/{{folder}}?resource="
                corrected = True
        if corrected:
            self.save_zones(zones)
        return zones

    def save_zones(self, zones):
        ZONES_DIR.mkdir(parents=True, exist_ok=True)
        self.config_file.write_text(json.dumps(zones, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def make_zone_from_cluster(cluster_name):
        # cluster_name: "cce-prod-st02"
        code = cluster_name.rsplit("-", 1)[-1]  # st02, tb01, tb04
        return {
            "path": f"cce-prod/cowork/clusters/{cluster_name}",
            "label": cluster_name,
            "url": f"https://argocd-{code}.cowork.ru/applications/cowork/{{folder}}?resource="
        }

    @staticmethod
    def get_remote_clusters(repo_root):
        clusters_path = repo_root / "cce-prod/cowork/clusters"
        if not clusters_path.exists():
            return []
        return sorted(d.name for d in clusters_path.iterdir() if d.is_dir())

    def discover_and_save_zones(self, repo_root):
        zones = [self.make_zone_from_cluster(c) for c in self.get_remote_clusters(repo_root)]
        zones += [dict(z) for z in EXTRA_ZONES if _zone_path_allowed(z["path"])]
        self.save_zones(zones)
        return zones

    def check_and_update_zones(self, repo_root, zones):
        remote_clusters = self.get_remote_clusters(repo_root)
        # Устаревшие зоны (больше не актуальны): убираем из конфига до проверки доступности.
        allowed_paths = {f"cce-prod/cowork/clusters/{c}" for c in remote_clusters}
        allowed_paths |= {z["path"] for z in EXTRA_ZONES}
        stale = [z for z in zones if z["path"] not in allowed_paths]
        if stale:
            zones[:] = [z for z in zones if z["path"] in allowed_paths]
            self.save_zones(zones)
            for z in stale:
                print_info(f"Удалена устаревшая зона из конфигурации: {z['label']}.")
        known = {z["path"].split("/")[-1] for z in zones}
        for zone in zones:
            if zone["path"].startswith("cce-prod/cowork/clusters/"):
                if zone["path"].split("/")[-1] not in remote_clusters:
                    print_error(f"Кластер {zone['label']} не доступен.")
            elif not (repo_root / zone["path"]).exists():
                print_error(f"Зона {zone['label']} не доступна (папка {zone['path']}).")
        # Нестандартные зоны: добавляем в конфиг, если их ещё нет,
        # либо синхронизируем поля (например, stand_prefixes), если они изменились.
        known_paths = {z["path"] for z in zones}
        for extra in EXTRA_ZONES:
            if not _zone_path_allowed(extra["path"]):
                print_error(f"Зона {extra['label']} пропущена: путь вне {ALLOWED_REPO_DIRS}.")
                continue
            if extra["path"] not in known_paths:
                zones.append(dict(extra))
                self.save_zones(zones)
                print_info(f"Добавлена зона: {extra['label']} ({extra['path']}).")
            else:
                for zone in zones:
                    if zone["path"] != extra["path"]:
                        continue
                    # JSON хранит кортежи как списки — нормализуем перед сравнением.
                    norm_zone = dict(zone)
                    norm_extra = dict(extra)
                    for key in ("stand_prefixes",):
                        if key in norm_zone:
                            norm_zone[key] = tuple(norm_zone[key])
                        if key in norm_extra:
                            norm_extra[key] = tuple(norm_extra[key])
                    if norm_zone != norm_extra:
                        zones[zones.index(zone)] = dict(extra)
                        self.save_zones(zones)
                        print_info(f"Обновлена зона: {extra['label']} ({extra['path']}).")
                    break

        updated = False
        for cluster in (c for c in remote_clusters if c not in known):
            print_warn(f"Обнаружен новый кластер: {cluster}")
            answer = input_with_timeout("Добавить в конфигурацию Finder новый кластер? (yes/no): ", INPUT_TIMEOUT)
            if answer and answer.lower() in ("y", "yes"):
                zones.append(self.make_zone_from_cluster(cluster))
                self.save_zones(zones)
                print_info(f"Кластер {cluster} добавлен в конфигурацию.")
                updated = True
            else:
                print_info(f"Кластер {cluster} пропущен.")
        if updated:
            print_info("Конфигурация обновлена.")

    def init(self, repo_root):
        if not self.config_file.exists():
            zones = self.discover_and_save_zones(repo_root)
            print_info(f"Создан файл zones_config.json с {len(zones)} кластерами.")
        else:
            zones = self.load_zones()
            self.check_and_update_zones(repo_root, zones)
        return zones

    def verify(self, repo_root, zones):
        """Самопроверка при старте: каждая зона существует, читаема и содержит конфиги."""
        print_info("Самопроверка доступности зон:")
        missing = []
        unreadable = []
        empty = []
        for zone in zones:
            base = repo_root / zone["path"]
            if not base.exists():
                missing.append(zone)
                print(f"  {_tag('ERROR', RED)} {zone['label']}: папка {zone['path']} не найдена")
                continue
            try:
                stand_prefixes = tuple(zone.get("stand_prefixes", ("cowork-",)))
                configs = [p for p in base.rglob(CONFIG) if p.parent.name.startswith(stand_prefixes)]
            except OSError as error:
                unreadable.append(zone)
                print(f"  {_tag('ERROR', RED)} {zone['label']}: папка {zone['path']} не читается ({error})")
                continue
            if not configs:
                empty.append(zone)
                print(f"  {_tag('WARN', YELLOW)} {zone['label']}: папка {zone['path']} не содержит config.yaml")
            else:
                print(f"  {_tag('OK', GREEN)} {zone['label']}: {len(configs)} конфиг(ов) в {zone['path']}")
        if missing or unreadable:
            print_error("Ошибка: некоторые зоны недоступны. Продолжение невозможно.")
            sys.exit(1)
        if empty:
            print(f"  {_tag('WARN', YELLOW)} В некоторых зонах нет конфигов — поиск по ним вернёт пусто.")
        print_info(f"Самопроверка завершена: зон проверено {len(zones)}, ошибок {len(missing) + len(unreadable)}.")


ZONES = ZoneManager(ZONES_CONFIG_FILE)

def resolve_zone(selected):
    """Зона из stand_dir: точное имя кластера или код в конце; поля EXTRA_ZONES сливаются."""
    cluster = selected.get("stand_dir", "")
    code = cluster.rsplit("-", 1)[-1] if cluster else ""
    for z in WORKSPACE_ZONES:
        if z["cluster"] == cluster or z["code"] == code:
            result = dict(z)
            for extra in EXTRA_ZONES:
                if extra["label"] == z["cluster"]:
                    result.update(extra)
                    break
            return result
    return None
