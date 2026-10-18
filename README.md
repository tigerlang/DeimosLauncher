# Deimos Launcher

Minimal Minecraft launcher written in Python. Version 0.1.

- Parallel downloads (`asyncio` + `aiohttp`), parallel SHA1 verification (`concurrent.futures`)
- Vanilla releases and snapshots, Fabric and Quilt loaders
- Mod search and install from Modrinth, with required dependencies
- Automatic Java selection per game version, one-click Java install (Adoptium)
- Offline accounts
- GUI: pywebview (WebView2, MSHTML fallback), PySide6 fallback

## Run from source

Python 3.12+ is required.

```
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

Force the Qt interface:

```
python main.py --qt
```

On Linux, pywebview needs a GTK or Qt backend; without it the launcher starts the Qt interface.

## Build

Local Windows build with PyInstaller:

```
build.bat
```

The release contains the Windows `.exe`, the Linux `.tar.gz` and `SHA256SUMS`.

## Data

Everything is stored in `%APPDATA%\.deimos` (`~/.deimos` on Linux and macOS):

Settings > Delete version removes a version with its loader profiles and modded instance, then cleans libraries and assets no other version uses.

## Mods

Select Fabric or Quilt on the Play tab, open Mods, search and install. Mods are stored per version and loader in `instances/<loader>-<version>/mods`.
