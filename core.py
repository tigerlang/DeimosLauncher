import asyncio
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import threading
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import aiohttp

APP_VERSION = "0.1"
MANIFEST_URL = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
ASSETS_URL = "https://resources.download.minecraft.net"
LOADER_META = {"fabric": "https://meta.fabricmc.net/v2", "quilt": "https://meta.quiltmc.org/v3"}
ADOPTIUM_URL = "https://api.adoptium.net/v3/binary/latest/{major}/ga/{os}/{arch}/jre/hotspot/normal/eclipse"

APPDATA = Path(os.environ.get("APPDATA") or Path.home())
ROOT = APPDATA / ".deimos"
VERSIONS_DIR = ROOT / "versions"
LIBRARIES_DIR = ROOT / "libraries"
ASSETS_DIR = ROOT / "assets"
RUNTIME_DIR = ROOT / "runtime"
INSTANCES_DIR = ROOT / "instances"
GAME_DIR = ROOT / "game"
SETTINGS_FILE = ROOT / "settings.json"
MANIFEST_CACHE = ROOT / "manifest.json"
LOG_FILE = ROOT / "game.log"

OS_NAME = {"Windows": "windows", "Darwin": "osx"}.get(platform.system(), "linux")
ARCH_X86 = sys.maxsize <= 2**32
ARCH_BITS = "32" if ARCH_X86 else "64"
JAVA_EXE = "java.exe" if OS_NAME == "windows" else "java"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

CHUNK_SIZE = 256 * 1024
FILE_BUFFER = 1024 * 1024
MAX_CONNECTIONS = 32
HASH_WORKERS = min(32, (os.cpu_count() or 4) * 2)
RETRIES = 3
JAVA_ERROR_MARKERS = ("UnsupportedClassVersionError", "URLClassLoader", "Unsupported major.minor")

DEFAULT_SETTINGS = {
    "username": "Player",
    "memory_mb": 2048,
    "java_path": "",
    "version": "",
    "loader": "vanilla",
    "snapshots": False,
}


class LauncherError(Exception):
    pass


@dataclass(frozen=True)
class FileTask:
    url: str
    path: Path
    sha1: str = ""
    size: int = 0


@dataclass
class State:
    stage: str = "Idle"
    done: int = 0
    total: int = 0
    error: str = ""
    busy: bool = False
    playing: bool = False


def instance_dir(version_id, loader):
    return GAME_DIR if loader == "vanilla" else INSTANCES_DIR / f"{loader}-{version_id}"


def load_settings():
    try:
        saved = json.loads(SETTINGS_FILE.read_text("utf-8"))
    except (OSError, ValueError):
        saved = {}
    return {**DEFAULT_SETTINGS, **{k: v for k, v in saved.items() if k in DEFAULT_SETTINGS}}


def save_settings(values):
    settings = {**DEFAULT_SETTINGS, **{k: values[k] for k in DEFAULT_SETTINGS if k in values}}
    ROOT.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2), "utf-8")
    return settings


def load_manifest():
    async def fetch():
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
            async with session.get(MANIFEST_URL) as response:
                response.raise_for_status()
                return await response.json(content_type=None)

    try:
        manifest = asyncio.run(fetch())
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
        if not MANIFEST_CACHE.exists():
            raise LauncherError("Cannot reach Mojang servers and no cached version list exists")
        return json.loads(MANIFEST_CACHE.read_text("utf-8"))
    ROOT.mkdir(parents=True, exist_ok=True)
    MANIFEST_CACHE.write_text(json.dumps(manifest), "utf-8")
    return manifest


def installed_versions():
    return sorted({p.parent.name for p in VERSIONS_DIR.glob("*/*.jar") if p.stem == p.parent.name}, reverse=True)


def open_folder(kind):
    path = VERSIONS_DIR if kind == "versions" else ROOT
    path.mkdir(parents=True, exist_ok=True)
    if OS_NAME == "windows":
        os.startfile(path)
    else:
        opener = "open" if OS_NAME == "osx" else "xdg-open"
        subprocess.Popen([opener, str(path)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def list_versions(include_snapshots):
    installed = set(installed_versions())
    try:
        manifest = load_manifest()
        entries = [
            {"id": v["id"], "type": v["type"]}
            for v in manifest["versions"]
            if include_snapshots or v["type"] == "release"
        ]
    except LauncherError:
        entries = [{"id": name, "type": "local"} for name in sorted(installed, reverse=True)]
    for entry in entries:
        entry["installed"] = entry["id"] in installed
    return entries


def collect_garbage():
    used_files, used_indexes, used_hashes = set(), set(), set()
    try:
        for path in VERSIONS_DIR.glob("*/*.json"):
            meta = json.loads(path.read_text("utf-8"))
            used_files.update(task.path for task in collect_libraries(meta)[0])
            if "assetIndex" in meta:
                used_indexes.add(meta["assetIndex"]["id"])
    except (OSError, ValueError, KeyError):
        return
    for index_id in used_indexes:
        try:
            index = json.loads((ASSETS_DIR / "indexes" / f"{index_id}.json").read_text("utf-8"))
        except (OSError, ValueError):
            continue
        used_hashes.update(obj["hash"] for obj in index["objects"].values())
    for file in LIBRARIES_DIR.rglob("*"):
        if file.is_file() and file not in used_files:
            file.unlink()
    for file in (ASSETS_DIR / "objects").rglob("*"):
        if file.is_file() and file.name not in used_hashes:
            file.unlink()
    for file in (ASSETS_DIR / "indexes").glob("*.json"):
        if file.stem not in used_indexes:
            file.unlink()
    for folder in (ASSETS_DIR / "virtual").glob("*"):
        if folder.name not in used_indexes:
            shutil.rmtree(folder, ignore_errors=True)
    for base in (LIBRARIES_DIR, ASSETS_DIR / "objects"):
        for folder in sorted(base.rglob("*"), reverse=True):
            if folder.is_dir() and not any(folder.iterdir()):
                folder.rmdir()


def delete_version(state, version_id):
    if not re.fullmatch(r"\w[\w.+-]*", version_id or ""):
        raise LauncherError("Invalid version id")
    state.stage = f"Deleting {version_id}"
    targets = [
        VERSIONS_DIR / version_id,
        *VERSIONS_DIR.glob(f"*-loader-*-{version_id}"),
        *(INSTANCES_DIR / f"{loader}-{version_id}" for loader in LOADER_META),
    ]
    for target in targets:
        shutil.rmtree(target, ignore_errors=True)
    state.stage = "Removing unused shared files"
    collect_garbage()


def is_valid(task):
    try:
        if task.size and task.path.stat().st_size != task.size:
            return False
        if not task.sha1:
            return task.path.exists()
        with task.path.open("rb", buffering=FILE_BUFFER) as file:
            return hashlib.file_digest(file, "sha1").hexdigest() == task.sha1
    except OSError:
        return False


class Downloader:
    def __init__(self, state):
        self.state = state

    async def sync(self, tasks):
        self.state.stage = "Verifying files"
        with ThreadPoolExecutor(HASH_WORKERS) as pool:
            checks = await asyncio.to_thread(lambda: list(pool.map(is_valid, tasks)))
        await self.download([task for task, valid in zip(tasks, checks) if not valid])

    async def download(self, tasks, stage="Downloading files"):
        if not tasks:
            return
        self.state.stage = stage
        self.state.done, self.state.total = 0, sum(task.size for task in tasks)
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=15, sock_read=30)
        slots = asyncio.Semaphore(MAX_CONNECTIONS)
        try:
            async with aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(limit=MAX_CONNECTIONS), timeout=timeout
            ) as session:
                async with asyncio.TaskGroup() as group:
                    for task in tasks:
                        group.create_task(self._fetch(session, slots, task))
        except ExceptionGroup as failure:
            raise failure.exceptions[0] from None

    async def _fetch(self, session, slots, task):
        task.path.parent.mkdir(parents=True, exist_ok=True)
        part = task.path.with_name(task.path.name + ".part")
        async with slots:
            for attempt in range(RETRIES):
                received = 0
                try:
                    async with session.get(task.url) as response:
                        response.raise_for_status()
                        with part.open("wb", buffering=FILE_BUFFER) as out:
                            async for chunk in response.content.iter_chunked(CHUNK_SIZE):
                                out.write(chunk)
                                received += len(chunk)
                                self.state.done += len(chunk)
                    part.replace(task.path)
                    return
                except (aiohttp.ClientError, asyncio.TimeoutError):
                    self.state.done -= received
                    if attempt == RETRIES - 1:
                        raise
                    await asyncio.sleep(attempt + 1)


def rule_matches(rule):
    if "features" in rule:
        return False
    target = rule.get("os")
    if not target:
        return True
    if "name" in target and target["name"] != OS_NAME:
        return False
    if "arch" in target and (target["arch"] == "x86") != ARCH_X86:
        return False
    if "version" in target and not re.match(target["version"], platform.version()):
        return False
    return True


def rules_allow(rules):
    if not rules:
        return True
    verdict = False
    for rule in rules:
        if rule_matches(rule):
            verdict = rule["action"] == "allow"
    return verdict


def maven_path(name):
    group, artifact, version, *classifier = name.split(":")
    suffix = f"-{classifier[0]}" if classifier else ""
    return f"{group.replace('.', '/')}/{artifact}/{version}/{artifact}-{version}{suffix}.jar"


def collect_libraries(meta):
    tasks, classpath, natives, seen = {}, [], [], set()
    for lib in meta["libraries"]:
        if not rules_allow(lib.get("rules")):
            continue
        group, artifact_id, _, *classifier_part = lib["name"].split(":")
        key = (group, artifact_id, *classifier_part)
        if key in seen:
            continue
        seen.add(key)
        downloads = lib.get("downloads", {})
        artifact = downloads.get("artifact")
        if not artifact and lib.get("url"):
            path = maven_path(lib["name"])
            artifact = {
                "path": path,
                "url": lib["url"].rstrip("/") + "/" + path,
                "sha1": lib.get("sha1", ""),
                "size": lib.get("size", 0),
            }
        if artifact and artifact.get("url"):
            path = LIBRARIES_DIR / artifact["path"]
            tasks[artifact["path"]] = FileTask(artifact["url"], path, artifact["sha1"], artifact["size"])
            classpath.append(str(path))
        classifier = lib.get("natives", {}).get(OS_NAME, "").replace("${arch}", ARCH_BITS)
        native = downloads.get("classifiers", {}).get(classifier)
        if native:
            path = LIBRARIES_DIR / native["path"]
            tasks[native["path"]] = FileTask(native["url"], path, native["sha1"], native["size"])
            natives.append((path, lib.get("extract", {}).get("exclude", [])))
    return list(tasks.values()), classpath, natives


def merge_meta(child, parent):
    merged = {**parent, **{k: v for k, v in child.items() if k not in ("libraries", "arguments")}}
    merged["libraries"] = child["libraries"] + parent["libraries"]
    if "arguments" in parent:
        merged["arguments"] = {
            key: parent["arguments"].get(key, []) + child.get("arguments", {}).get(key, [])
            for key in ("jvm", "game")
        }
    return merged


def extract_natives(natives, target):
    target.mkdir(parents=True, exist_ok=True)
    for jar, excludes in natives:
        with zipfile.ZipFile(jar) as archive:
            for member in archive.namelist():
                if not member.endswith("/") and not member.startswith(tuple(excludes)):
                    archive.extract(member, target)


def legacy_assets_dir(index, index_id):
    if index.get("map_to_resources"):
        return GAME_DIR / "resources"
    if index.get("virtual"):
        return ASSETS_DIR / "virtual" / index_id
    return None


def copy_legacy_assets(index, target):
    for name, obj in index["objects"].items():
        dest = target / name
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ASSETS_DIR / "objects" / obj["hash"][:2] / obj["hash"], dest)


def java_major(executable):
    try:
        result = subprocess.run(
            [executable, "-version"], capture_output=True, text=True, errors="replace",
            timeout=15, creationflags=NO_WINDOW, stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r'version "(\d+)(?:\.(\d+))?', result.stderr)
    if not match:
        return None
    major = int(match.group(1))
    return int(match.group(2) or 0) if major == 1 else major


def java_fits(major, required):
    return major == 8 if required == 8 else major >= required


def java_candidates():
    yield from map(str, RUNTIME_DIR.glob(f"*/**/bin/{JAVA_EXE}"))
    home = os.environ.get("JAVA_HOME")
    if home:
        yield str(Path(home) / "bin" / JAVA_EXE)
    on_path = shutil.which("java")
    if on_path:
        yield on_path
    for variable in ("ProgramFiles", "ProgramFiles(x86)"):
        if os.environ.get(variable):
            yield from map(str, Path(os.environ[variable]).glob("*/*/bin/java.exe"))
    yield from map(str, (APPDATA / ".minecraft" / "runtime").glob("*/*/*/bin/java.exe"))
    for pattern in ("usr/lib/jvm/*/bin/java", "Library/Java/JavaVirtualMachines/*/Contents/Home/bin/java"):
        yield from map(str, Path("/").glob(pattern))


def find_java(required, custom):
    if custom:
        major = java_major(custom)
        if not major or not java_fits(major, required):
            raise LauncherError(f"Configured Java is invalid or does not match Java {required}")
        return custom
    found, probed = {}, set()
    for candidate in java_candidates():
        real = os.path.realpath(candidate)
        if real in probed:
            continue
        probed.add(real)
        major = java_major(candidate)
        if major and java_fits(major, required):
            found[candidate] = major
    if not found:
        raise LauncherError(f"Java {required} was not found. Install it in Settings")
    return min(found, key=lambda path: (found[path] != required, found[path]))


def extract_archive(archive, target):
    shutil.rmtree(target, ignore_errors=True)
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(target)
    else:
        with tarfile.open(archive) as bundle:
            bundle.extractall(target, filter="data")


async def install_java(state, major):
    machine = platform.machine().lower()
    arch = "aarch64" if machine in ("arm64", "aarch64") else "x86" if ARCH_X86 else "x64"
    adoptium_os = {"windows": "windows", "osx": "mac", "linux": "linux"}[OS_NAME]
    archive = RUNTIME_DIR / f"java-{major}.download"
    url = ADOPTIUM_URL.format(major=major, os=adoptium_os, arch=arch)
    await Downloader(state).download([FileTask(url, archive)], f"Downloading Java {major}")
    state.stage = f"Extracting Java {major}"
    await asyncio.to_thread(extract_archive, archive, RUNTIME_DIR / f"java-{major}")
    archive.unlink()
    if not any((RUNTIME_DIR / f"java-{major}").rglob(f"bin/{JAVA_EXE}")):
        raise LauncherError(f"Java {major} is not available for this platform")


def expand_arguments(entries):
    values = []
    for entry in entries:
        if isinstance(entry, str):
            values.append(entry)
        elif rules_allow(entry.get("rules")):
            value = entry["value"]
            values.extend([value] if isinstance(value, str) else value)
    return values


def substitute(values, variables):
    return [re.sub(r"\$\{(\w+)\}", lambda m: variables.get(m.group(1), ""), value) for value in values]


def build_command(java, meta, variables, memory_mb):
    if "arguments" in meta:
        jvm = expand_arguments(meta["arguments"]["jvm"])
        game = expand_arguments(meta["arguments"]["game"])
    else:
        jvm = ["-Djava.library.path=${natives_directory}", "-cp", "${classpath}"]
        game = meta["minecraftArguments"].split()
    return [java, f"-Xmx{memory_mb}M", *substitute(jvm, variables), meta["mainClass"], *substitute(game, variables)]


def offline_uuid(username):
    digest = hashlib.md5(f"OfflinePlayer:{username}".encode()).digest()
    return str(uuid.UUID(bytes=digest, version=3))


def exit_message(code, required):
    text = LOG_FILE.read_text("utf-8", errors="replace") if LOG_FILE.exists() else ""
    if any(marker in text for marker in JAVA_ERROR_MARKERS):
        return f"Java version mismatch: this version needs Java {required}. Install it in Settings"
    return f"Game exited with code {code}. Log: {LOG_FILE}"


class Launcher:
    def __init__(self):
        self.state = State()

    def submit(self, job):
        state = self.state
        if state.busy:
            return
        state.busy, state.error, state.done, state.total = True, "", 0, 0
        threading.Thread(target=self._guarded, args=(job,), daemon=True).start()

    def _guarded(self, job):
        state = self.state
        try:
            job()
        except LauncherError as error:
            state.error = str(error)
        except Exception as error:
            state.error = f"{type(error).__name__}: {error}"
        finally:
            state.busy = state.playing = False
            state.stage = "Idle"

    def play(self, version_id, loader, username, memory_mb, java_path):
        state = self.state
        if state.busy:
            return
        state.error = ""
        if not version_id:
            state.error = "No version selected"
        elif loader != "vanilla" and loader not in LOADER_META:
            state.error = f"Unknown loader: {loader}"
        elif not re.fullmatch(r"[A-Za-z0-9_]{3,16}", username or ""):
            state.error = "Username must be 3-16 letters, digits or underscores"
        if state.error:
            return
        memory = max(512, int(memory_mb))
        self.submit(lambda: self._play(version_id, loader, username, memory, java_path))

    def delete_version(self, version_id):
        self.submit(lambda: delete_version(self.state, version_id))

    def install_java(self, major):
        self.submit(lambda: asyncio.run(install_java(self.state, int(major))))

    def _play(self, version_id, loader, username, memory_mb, java_path):
        state = self.state
        meta, profile_id, index_id, classpath, legacy = asyncio.run(self._install(version_id, loader))
        state.stage = "Starting game"
        required = meta.get("javaVersion", {}).get("majorVersion", 8)
        java = find_java(required, java_path)
        game_dir = instance_dir(version_id, loader)
        variables = {
            "auth_player_name": username,
            "auth_uuid": offline_uuid(username),
            "auth_access_token": "0",
            "auth_session": "0",
            "user_type": "legacy",
            "user_properties": "{}",
            "clientid": "",
            "auth_xuid": "",
            "version_name": profile_id,
            "version_type": meta.get("type", "release"),
            "game_directory": str(game_dir),
            "assets_root": str(ASSETS_DIR),
            "assets_index_name": index_id,
            "game_assets": str(legacy or ASSETS_DIR),
            "natives_directory": str(VERSIONS_DIR / version_id / "natives"),
            "library_directory": str(LIBRARIES_DIR),
            "classpath": os.pathsep.join(classpath),
            "classpath_separator": os.pathsep,
            "launcher_name": "deimos",
            "launcher_version": APP_VERSION,
        }
        command = build_command(java, meta, variables, memory_mb)
        game_dir.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("wb") as log:
            process = subprocess.Popen(
                command, cwd=game_dir, stdin=subprocess.DEVNULL, stdout=log,
                stderr=subprocess.STDOUT, creationflags=NO_WINDOW,
            )
            state.stage, state.playing = "Game running", True
            code = process.wait()
        if code:
            state.error = exit_message(code, required)

    async def _vanilla_meta(self, version_id, downloader):
        meta_path = VERSIONS_DIR / version_id / f"{version_id}.json"
        if not meta_path.exists():
            manifest = await asyncio.to_thread(load_manifest)
            entry = next((v for v in manifest["versions"] if v["id"] == version_id), None)
            if entry is None:
                raise LauncherError(f"Unknown version: {version_id}")
            await downloader.download([FileTask(entry["url"], meta_path, entry["sha1"])])
        return json.loads(meta_path.read_text("utf-8"))

    async def _loader_meta(self, loader, version_id, downloader):
        base = LOADER_META[loader]
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
                async with session.get(f"{base}/versions/loader/{version_id}") as response:
                    response.raise_for_status()
                    entries = await response.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            entries = []
        stable = [e["loader"]["version"] for e in entries if e["loader"].get("stable", "-" not in e["loader"]["version"])]
        if stable:
            profile_id = f"{loader}-loader-{stable[0]}-{version_id}"
            path = VERSIONS_DIR / profile_id / f"{profile_id}.json"
            if not path.exists():
                url = f"{base}/versions/loader/{version_id}/{stable[0]}/profile/json"
                await downloader.download([FileTask(url, path)])
        else:
            local = sorted(VERSIONS_DIR.glob(f"{loader}-loader-*-{version_id}/*.json"))
            if not local:
                raise LauncherError(f"No {loader.title()} loader available for {version_id}")
            path = local[-1]
        return json.loads(path.read_text("utf-8"))

    async def _install(self, version_id, loader):
        downloader = Downloader(self.state)
        version_dir = VERSIONS_DIR / version_id
        self.state.stage = "Fetching version data"
        meta = await self._vanilla_meta(version_id, downloader)
        if loader != "vanilla":
            meta = merge_meta(await self._loader_meta(loader, version_id, downloader), meta)

        asset_index = meta["assetIndex"]
        index_path = ASSETS_DIR / "indexes" / f"{asset_index['id']}.json"
        await downloader.sync([FileTask(asset_index["url"], index_path, asset_index["sha1"], asset_index["size"])])
        index = json.loads(index_path.read_text("utf-8"))

        client = meta["downloads"]["client"]
        client_jar = version_dir / f"{version_id}.jar"
        library_tasks, classpath, natives = collect_libraries(meta)
        objects = {obj["hash"]: obj["size"] for obj in index["objects"].values()}
        asset_tasks = [
            FileTask(f"{ASSETS_URL}/{h[:2]}/{h}", ASSETS_DIR / "objects" / h[:2] / h, h, size)
            for h, size in objects.items()
        ]
        client_task = FileTask(client["url"], client_jar, client["sha1"], client["size"])
        await downloader.sync([client_task, *library_tasks, *asset_tasks])

        self.state.stage = "Preparing files"
        extract_natives(natives, version_dir / "natives")
        legacy = legacy_assets_dir(index, asset_index["id"])
        if legacy:
            copy_legacy_assets(index, legacy)
        return meta, meta["id"], asset_index["id"], [*classpath, str(client_jar)], legacy
