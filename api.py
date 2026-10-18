import asyncio
from dataclasses import asdict

import mods
from core import Launcher, installed_versions, list_versions, load_settings, open_folder, save_settings


class Api:
    def __init__(self):
        self._launcher = Launcher()

    def get_settings(self):
        return load_settings()

    def save_settings(self, values):
        return save_settings(values)

    def get_versions(self, include_snapshots):
        return list_versions(bool(include_snapshots))

    def installed_versions(self):
        return installed_versions()

    def delete_version(self, version):
        self._launcher.delete_version(version)

    def open_folder(self, kind):
        open_folder(kind)

    def play(self, version, loader, username, memory_mb, java_path):
        self._launcher.play(version, loader, username, memory_mb, java_path)

    def install_java(self, major):
        self._launcher.install_java(major)

    def search_mods(self, query, version, loader):
        return mods.search_mods(query, version, loader)

    def list_mods(self, version, loader):
        return mods.list_mods(version, loader)

    def install_mod(self, project_id, version, loader):
        state = self._launcher.state
        self._launcher.submit(lambda: asyncio.run(mods.install_mod(state, project_id, version, loader)))

    def remove_mod(self, project_id, version, loader):
        mods.remove_mod(project_id, version, loader)

    def get_state(self):
        return asdict(self._launcher.state)
