import sys
from pathlib import Path

from api import Api

BASE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
WEBVIEW2_CLIENT_ID = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"


def webview2_installed():
    import winreg

    locations = (
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\EdgeUpdate\Clients"),
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\EdgeUpdate\Clients"),
    )
    for hive, base in locations:
        try:
            with winreg.OpenKey(hive, f"{base}\\{WEBVIEW2_CLIENT_ID}") as key:
                if winreg.QueryValueEx(key, "pv")[0] not in ("", "0.0.0.0"):
                    return True
        except OSError:
            continue
    return False


def run_web(api):
    import webview

    renderer = None
    if sys.platform == "win32":
        renderer = "edgechromium" if webview2_installed() else "mshtml"
    webview.create_window(
        "Deimos Launcher",
        str(BASE_DIR / "ui" / "index.html"),
        js_api=api,
        width=980,
        height=620,
        min_size=(860, 540),
        background_color="#1a1b1f",
        text_select=False,
    )
    webview.start(gui=renderer)


def run_qt(api):
    from qt_ui import run

    run(api)


def main():
    api = Api()
    if "--qt" in sys.argv:
        run_qt(api)
        return
    try:
        run_web(api)
    except Exception:
        run_qt(api)


if __name__ == "__main__":
    main()
