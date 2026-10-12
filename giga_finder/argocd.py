from giga_finder.constants import *
from giga_finder.runtime import open_url
from giga_finder.ui import print_info

def _argocd_base(zone_code):
    return f"https://argocd-{zone_code}.cowork.ru"


def open_pod_exec(zone_code, stand_name, app_suffix, app_template=None, argocd_base=None):
    """Страница приложения yggdrasil/heimdall в ArgoCD; если все поды в одном приложении — браузер открывается один раз."""
    if app_template:
        app_path = app_template.format(stand_name=stand_name, app_suffix=app_suffix)
        suffix_dependent = "{app_suffix}" in app_template
    else:
        app_path = f"{stand_name}/{stand_name}-{app_suffix}"
        suffix_dependent = True
    base = argocd_base or _argocd_base(zone_code)
    if suffix_dependent:
        open_url(f"{base}/applications/{app_path}?resource=")
        print_info(f"В списке приложения выберите под {app_suffix}* и откройте вкладку exec.")
    else:
        if not getattr(open_pod_exec, "_opened", None):
            open_url(f"{base}/applications/{app_path}?resource=")
            open_pod_exec._opened = True
            print_info(f"Приложение {app_path} открыто — выберите под {app_suffix}* и откройте вкладку exec.")
        else:
            print_info(f"В уже открытом приложении {app_path} переключитесь на под {app_suffix}* (вкладка exec).")


def open_workspace_exec(zone_code, script_content, script_name, argocd_base=None):
    """Скрипт в exec/ + страница приложения ArgoCD (под debug-ubuntu выбирается вручную)."""
    EXEC_DIR.mkdir(parents=True, exist_ok=True)
    out = EXEC_DIR / script_name
    out.write_text(script_content, encoding="utf-8")
    print_info(f"Скрипт сохранён: {out}")
    base = argocd_base or _argocd_base(zone_code)
    open_url(f"{base}/applications/{ARGOCD_APP}?resource=")
    print_info(f"В списке приложения выберите под {DEBUG_POD_PREFIX}* (вкладка exec), "
               f"скопируйте содержимое {script_name} в веб-терминал и запустите: python3 {script_name}")

