"""Загрузка Finder для тестов: пакет giga_finder или монолит finder-pro.py."""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load():
    package_init = ROOT / "giga_finder" / "__init__.py"
    if package_init.exists():
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        import giga_finder
        return giga_finder
    path = ROOT / "finder-pro.py"
    spec = importlib.util.spec_from_file_location("finder_pro", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["finder_pro"] = module
    spec.loader.exec_module(module)
    return module


def patch_attr(mod, name, value):
    """Подмена имени в пакете и во всех уже загруженных подмодулях.

    Функции ищут глобалы в своём модуле, поэтому подмена только на фасаде
    пакета не влияет на вызовы внутри giga_finder.*.
    """
    setattr(mod, name, value)
    for module_name, submodule in list(sys.modules.items()):
        if submodule is None:
            continue
        if module_name == "giga_finder" or module_name.startswith("giga_finder."):
            setattr(submodule, name, value)
