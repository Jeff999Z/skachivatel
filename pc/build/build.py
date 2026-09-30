"""
Сборка установщика для Windows:  python pc/build/build.py

1) скачивает официальные движки в pc/tools: yt-dlp.exe, gallery-dl.exe, ffmpeg (BtbN, GPL);
2) упаковывает программу PyInstaller'ом в pc/build/dist/Skachivatel/;
3) собирает установщик Inno Setup → dist/Skachivatel-Setup-<версия>.exe (нужен Inno Setup 6).
"""
import io
import os
import shutil
import subprocess
import sys
import zipfile

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
PC = os.path.dirname(HERE)
ROOT = os.path.dirname(PC)
TOOLS = os.path.join(PC, "tools")
VERSION = open(os.path.join(PC, "VERSION"), encoding="utf-8").read().strip()

ENGINES = {
    "yt-dlp.exe": "https://github.com/yt-dlp/yt-dlp-nightly-builds/releases/latest/download/yt-dlp.exe",
    # готовые .exe gallery-dl публикуются в официальном репозитории сборок gdl-org/builds
    "gallery-dl.exe": "https://github.com/gdl-org/builds/releases/latest/download/gallery-dl_windows.exe",
}
FFMPEG_ZIP = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
ISCC = [r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe", r"C:\Program Files\Inno Setup 6\ISCC.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe")]


def step(t):
    print(f"\n=== {t}", flush=True)


def fetch_tools():
    os.makedirs(os.path.join(TOOLS, "ffmpeg"), exist_ok=True)
    for name, url in ENGINES.items():
        path = os.path.join(TOOLS, name)
        if not os.path.exists(path) or open(path, "rb").read(2) != b"MZ":
            print(f"качаю {name}…", flush=True)
            r = requests.get(url, timeout=300)
            r.raise_for_status()
            if r.content[:2] != b"MZ":  # не .exe (например, страница «Not Found») — останавливаем сборку
                raise SystemExit(f"{name}: скачался не исполняемый файл — проверьте адрес {url}")
            with open(path, "wb") as f:
                f.write(r.content)
    ff = os.path.join(TOOLS, "ffmpeg", "ffmpeg.exe")
    if not os.path.exists(ff):
        print("качаю ffmpeg…", flush=True)
        z = zipfile.ZipFile(io.BytesIO(requests.get(FFMPEG_ZIP, timeout=600).content))
        for n in z.namelist():
            if n.endswith(("/bin/ffmpeg.exe", "/bin/ffprobe.exe")):
                with open(os.path.join(TOOLS, "ffmpeg", os.path.basename(n)), "wb") as f:
                    f.write(z.read(n))


def make_icon():
    """Значок: синий скруглённый квадрат со стрелкой вниз (как в Android-приложении)."""
    from PIL import Image, ImageDraw
    ico = os.path.join(HERE, "icon.ico")
    if os.path.exists(ico):
        return ico
    img = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((8, 8, 248, 248), 56, fill=(59, 91, 219, 255))
    w = 22
    d.line((128, 62, 128, 162), fill="white", width=w)
    d.line((84, 122, 128, 166, 172, 122), fill="white", width=w, joint="curve")
    d.line((70, 196, 186, 196), fill="white", width=w)
    img.save(ico, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    return ico


def pyinstaller(ico):
    dist, work = os.path.join(HERE, "dist"), os.path.join(HERE, "work")
    shutil.rmtree(os.path.join(dist, "Skachivatel"), ignore_errors=True)
    subprocess.check_call([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", "--name", "Skachivatel",
        "--icon", ico, "--distpath", dist, "--workpath", work, "--specpath", work,
        "--add-data", f"{os.path.join(PC, 'app_ui.html')};.",
        "--add-data", f"{os.path.join(PC, 'VERSION')};.",
        "--hidden-import", "telethon", "--hidden-import", "cryptg", "--hidden-import", "socks",
        # окно «Войти на сайт»: pywebview + движок Edge WebView2 (через pythonnet)
        "--collect-all", "webview", "--hidden-import", "clr", "--hidden-import", "login_window",
        "--collect-submodules", "telethon", "--collect-data", "qrcode",
        "--paths", PC, os.path.join(PC, "app.py")])
    out = os.path.join(dist, "Skachivatel")
    shutil.copytree(TOOLS, os.path.join(out, "tools"), dirs_exist_ok=True)
    for f in ("LICENSE", "README.md"):
        if os.path.exists(os.path.join(ROOT, f)):
            shutil.copy2(os.path.join(ROOT, f), out)
    return out


def inno():
    iscc = next((p for p in ISCC if os.path.exists(p)), None)
    if not iscc:
        print("Inno Setup 6 не найден — установщик не собран (программа лежит в pc/build/dist/Skachivatel).")
        return
    subprocess.check_call([iscc, f"/DVersion={VERSION}", os.path.join(HERE, "installer.iss")])


if __name__ == "__main__":
    step("Движки"); fetch_tools()
    step("Значок"); ico = make_icon()
    step("Упаковка программы"); pyinstaller(ico)
    step("Установщик"); inno()
    print("\nГотово.")
