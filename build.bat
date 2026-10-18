@echo off
python -m pip install -r requirements.txt
python -m PyInstaller --noconfirm --onefile --windowed --name DeimosLauncher --add-data "ui;ui" --collect-all webview --collect-all pythonnet --collect-all clr_loader --hidden-import clr main.py
