"""Общие константы Finder. Пути считаются от корня проекта, не от пакета."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

VERSION = "v4.3.0"
INPUT_TIMEOUT = 300

REPO_PATH = Path.home() / "Documents/Giga/cowork/argocd"
REPO_LOCAL_FILE = ROOT / "repo_local_path.txt"
REPO_URL = "git@git.sberdevices.ru:gigabb/infra/cowork/argocd.git"
DSN_CACHE_PATH = Path.home() / ".gigafinder" / "dsn-cache.json"
FAQ_AGENT_URL = "https://cowork.gigab2b.ru/workspaces/c63c8b9f-bdf0-4805-a314-b8da3eaeaea7"
CONFIG = "config.yaml"
ZONES_DIR = ROOT / "zones"
ZONES_CONFIG_FILE = ZONES_DIR / "zones_config.json"
EXEC_DIR = ROOT / "exec"
ACCOUNTS_DIR = ROOT / "accounts"
REPO_CHECK_INTERVAL = 15 * 60
REPO_FAST_CHECK_INTERVAL = 30
REPO_FAST_CHECK_THRESHOLD = 10

WORKSPACE_ZONES = [
    {"code": "st02", "cluster": "cce-prod-st02"},
    {"code": "tb01", "cluster": "cce-prod-tb01"},
    {"code": "sdb-tb01", "cluster": "cce-sdb-tb01-stands"},
    {"code": "tb04", "cluster": "cce-prod-tb04"},
]
EXTRA_ZONES = [
    {
        "path": "cce-sdb-tb01/cowork/stands",
        "label": "cce-sdb-tb01-stands",
        "url": "https://argocd.cowork.gigab2b.ru/applications/cowork/{folder}?resource=",
        "stand_prefixes": ("sber-", "cowork"),
        "app_template": "cowork/{stand_name}-{app_suffix}",
        "argocd_base": "https://argocd.cowork.gigab2b.ru",
        "links_app": "cowork-heimdall",
    },
]
ALLOWED_REPO_DIRS = ("cce-prod", "cce-prod-tb01", "cce-sdb-tb01")
ARGOCD_APP = "cowork/cowork"
DEBUG_POD_PREFIX = "debug-ubuntu"

BASE_WORKSPACE_IDS = [
    "f7a1d8cc-63d8-4063-a9b0-345cad57b5d6",
    "f4d1b8a9-12b2-4cd8-af5c-3d03b459d24e",
    "81782a49-c2a8-4c8a-8b06-03c1c61201b0",
    "5d261c97-8979-41b5-9970-b6185518f6d0",
    "ede5105a-721f-46cc-a529-462353a45855",
    "f77ce42b-9f5f-4870-a5b4-c46a0e6072c4",
    "9d5999da-fbcb-4278-ad9b-a5c90e686f69",
]
DEMO_STAND_SUBDOMAIN = "demo"
DEMO_YGGDRASIL_URL = "postgresql://yggdrasil:22C3LZ44HU0NBvlLj32aQ7IA4S71nmpqLpa9RA0zBIvMj8Bg@yggdrasil-postgres-rw.cowork-st02-0503.svc.cluster.local:5432/yggdrasil"
DEMO_HEIMDALL_URL = "postgresql://heimdall_auth:8Kmq71GyU1ezPmkys8mczPHbh62pxhMnsWmHi0efSmfuDEPR@heimdall-auth-postgres-rw.cowork-st02-0503.svc.cluster.local:5432/heimdall_auth"

WHITE = "\033[97m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
CYAN = "\033[96m"
MAGENTA = "\033[95m"
GRAY = "\033[90m"
RESET = "\033[0m"
