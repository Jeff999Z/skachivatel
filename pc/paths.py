"""
Где что лежит — одинаково для запуска из исходников и для установленной версии (PyInstaller).

  программа (окно, картинки)  APP_DIR    — рядом с кодом / внутри .exe
  инструменты (ffmpeg…)       TOOLS_DIR  — <папка установки>\\tools
  данные пользователя         DATA_DIR   — %APPDATA%\\Скачиватель (настройки, история, входы)
  обновляемые движки          ENGINES    — %APPDATA%\\Скачиватель\\engines (yt-dlp.exe, gallery-dl.exe)
"""
import os
import shutil
import subprocess
import sys

FROZEN = getattr(sys, "frozen", False)
APP_DIR = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
INSTALL_DIR = os.path.dirname(sys.executable) if FROZEN else os.path.dirname(os.path.abspath(__file__))
TOOLS_DIR = os.path.join(INSTALL_DIR, "tools")
DATA_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "Скачиватель")
ENGINES = os.path.join(DATA_DIR, "engines")
FFMPEG_DIR = os.path.join(TOOLS_DIR, "ffmpeg")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

os.makedirs(DATA_DIR, exist_ok=True)

ENGINE_EXE = {"yt_dlp": "yt-dlp.exe", "gallery_dl": "gallery-dl.exe"}


def data(name):
    return os.path.join(DATA_DIR, name)


def engine_path(module):
    """Путь к обновляемой копии движка (копируется из установки при первом запуске)."""
    exe = ENGINE_EXE[module]
    mine = os.path.join(ENGINES, exe)
    if not _is_exe(mine):
        bundled = os.path.join(TOOLS_DIR, exe)
        if _is_exe(bundled):
            os.makedirs(ENGINES, exist_ok=True)
            shutil.copy2(bundled, mine)
    return mine if _is_exe(mine) else None


def _is_exe(path):
    try:
        with open(path, "rb") as f:
            return f.read(2) == b"MZ"
    except OSError:
        return False


def cmd(args):
    """["yt_dlp", *аргументы] → команда запуска: отдельный .exe движка или python -m (из исходников)."""
    module, rest = args[0], args[1:]
    if module in ENGINE_EXE:
        exe = engine_path(module)
        if exe and (FROZEN or not _has_module(module)):
            return [exe] + rest
    return [sys.executable, "-m", module] + rest


def _has_module(name):
    import importlib.util
    return importlib.util.find_spec(name) is not None


def tool_env():
    return dict(os.environ, PYTHONIOENCODING="utf-8", PATH=FFMPEG_DIR + os.pathsep + os.environ.get("PATH", ""))


def launch_command(hidden=False):
    """Как запустить само приложение (для автозапуска)."""
    if FROZEN:
        return sys.executable, "--hidden" if hidden else ""
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    app = os.path.join(INSTALL_DIR, "app.py")
    return pyw, f'"{app}"' + (" --hidden" if hidden else "")
