import asyncio
import json

import aiohttp

from core import Downloader, FileTask, LauncherError, instance_dir

API_URL = "https://api.modrinth.com/v2"
HEADERS = {"User-Agent": "DeimosLauncher/0.1"}
LOADER_CATEGORIES = {"fabric": ["fabric"], "quilt": ["quilt", "fabric"]}
REGISTRY_NAME = "deimos-mods.json"


def check_loader(loader):
    if loader not in LOADER_CATEGORIES:
        raise LauncherError("Mods require the Fabric or Quilt loader")


def mods_dir(game_version, loader):
    return instance_dir(game_version, loader) / "mods"


def read_registry(directory):
    try:
        return json.loads((directory / REGISTRY_NAME).read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def write_registry(directory, registry):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / REGISTRY_NAME).write_text(json.dumps(registry, indent=2), "utf-8")


async def get_json(session, path, params=None):
    async with session.get(API_URL + path, params=params) as response:
        response.raise_for_status()
        return await response.json()


def new_session():
    return aiohttp.ClientSession(headers=HEADERS, timeout=aiohttp.ClientTimeout(total=30))


def search_mods(query, game_version, loader):
    check_loader(loader)
    facets = [
        ["project_type:mod"],
        [f"versions:{game_version}"],
        [f"categories:{category}" for category in LOADER_CATEGORIES[loader]],
        ["client_side:required", "client_side:optional"],
    ]

    async def run():
        async with new_session() as session:
            return await get_json(session, "/search", {"query": query, "facets": json.dumps(facets), "limit": 25})

    hits = asyncio.run(run())["hits"]
    return [
        {
            "id": hit["project_id"],
            "title": hit["title"],
            "description": hit["description"],
            "author": hit["author"],
            "downloads": hit["downloads"],
        }
        for hit in hits
    ]


def list_mods(game_version, loader):
    check_loader(loader)
    directory = mods_dir(game_version, loader)
    registry = read_registry(directory)
    return [
        {"id": pid, "title": entry["title"], "dependency": entry["dependency"]}
        for pid, entry in registry.items()
        if (directory / entry["file"]).exists()
    ]


def remove_mod(project_id, game_version, loader):
    check_loader(loader)
    directory = mods_dir(game_version, loader)
    registry = read_registry(directory)
    entry = registry.pop(project_id, None)
    if entry:
        (directory / entry["file"]).unlink(missing_ok=True)
        write_registry(directory, registry)


async def latest_version(session, project_id, game_version, loader):
    params = {
        "game_versions": json.dumps([game_version]),
        "loaders": json.dumps(LOADER_CATEGORIES[loader]),
    }
    versions = await get_json(session, f"/project/{project_id}/version", params)
    releases = [v for v in versions if v["version_type"] == "release"]
    if not (releases or versions):
        raise LauncherError(f"No compatible version of project {project_id} for {game_version}")
    return (releases or versions)[0]


async def install_mod(state, project_id, game_version, loader):
    check_loader(loader)
    directory = mods_dir(game_version, loader)
    registry = read_registry(directory)
    state.stage = "Resolving mods"
    tasks, queue, seen = [], [(project_id, False)], set()
    async with new_session() as session:
        while queue:
            pid, dependency = queue.pop()
            if pid in seen or (dependency and pid in registry):
                continue
            seen.add(pid)
            version = await latest_version(session, pid, game_version, loader)
            file = next((f for f in version["files"] if f["primary"]), version["files"][0])
            project = await get_json(session, f"/project/{pid}")
            previous = registry.get(pid)
            if previous and previous["file"] != file["filename"]:
                (directory / previous["file"]).unlink(missing_ok=True)
            registry[pid] = {"title": project["title"], "file": file["filename"], "dependency": dependency}
            tasks.append(FileTask(file["url"], directory / file["filename"], file["hashes"]["sha1"], file["size"]))
            queue.extend(
                (dep["project_id"], True)
                for dep in version["dependencies"]
                if dep["dependency_type"] == "required" and dep.get("project_id")
            )
    await Downloader(state).download(tasks, "Downloading mods")
    write_registry(directory, registry)
