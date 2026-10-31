"""Tool versions: find newer versions of installed tools and add them side by side.

The version a tool ships with (built-in) or was installed at (tool pack) always
stays. Extra versions go in $NTG_HOME/tool_versions/<tool>/<version>/tool.yml:
the same tool definition with another container (built-in tools: another
BioContainers tag from quay.io) or another commit of the pack's repo (packs).
A workflow step picks one with `tool_version:`; without it, the default is used.

A version that was added is "untested" until a run that used it succeeds.
"""
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import urllib.request
from pathlib import Path

from . import catalog, toolpacks

QUAY = "quay.io/biocontainers/"
CACHE = catalog.HOME / "tool_updates.json"
DAY = dt.timedelta(days=1)
_check_lock = threading.Lock()


def _versions_dir():
    return catalog.HOME / "tool_versions"


def _vkey(v):
    """Sort key for version strings: 1.10.0 > 1.9.2; text parts sort low."""
    return [(int(p), "") if p.isdigit() else (-1, p) for p in re.split(r"[.\-_+]", str(v))]


def _safe(v):
    return re.sub(r"[^A-Za-z0-9._+-]", "_", str(v))


def kind(tool):
    image = str(tool.get("container", ""))
    if image.startswith(QUAY) and not image.startswith(QUAY + "python:"):  # plain python = pipeline scripts
        return "biocontainers"
    if tool.get("source", {}).get("repo"):
        return "git"
    return None  # part of the pipeline itself (e.g. the summary script): no separate versions


# ---------------------------------------------------------------- finding versions

def _quay_versions(tool):
    repo = tool["container"][len(QUAY):].split(":")[0]
    url = f"https://quay.io/api/v1/repository/biocontainers/{repo}/tag/?limit=100&onlyActiveTags=true"
    tags, page = [], 1
    while page <= 5:
        with urllib.request.urlopen(f"{url}&page={page}", timeout=20) as r:
            d = json.load(r)
        tags += [t["name"] for t in d.get("tags", [])]
        if not d.get("has_additional"):
            break
        page += 1
    best = {}  # version -> newest build tag
    for tag in tags:
        if "--" not in tag:
            continue
        ver, build = tag.split("--", 1)
        tail = build.rsplit("_", 1)
        num = int(tail[1]) if len(tail) == 2 and tail[1].isdigit() else 0
        if ver not in best or num > best[ver][1]:
            best[ver] = (tag, num)
    return [{"version": v, "choice": f"{QUAY}{repo}:{t}", "label": f"{v}  ({t})"}
            for v, (t, _) in sorted(best.items(), key=lambda x: _vkey(x[0]), reverse=True)]


def _git_versions(tool):
    src = tool["source"]
    p = subprocess.run(["git", "ls-remote", src["repo"]], cwd="/",  # needs no local repo
                       capture_output=True, text=True, timeout=60, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    if p.returncode:
        raise RuntimeError(p.stderr.strip() or "git ls-remote failed")
    refs = [line.split("\t") for line in p.stdout.splitlines()]
    head = next((sha for sha, ref in refs if ref == "HEAD"), None)  # the repo's main branch
    out, seen = [], set()
    for sha, ref in refs:
        if not ref.startswith(("refs/heads/", "refs/tags/")) or sha in seen:
            continue
        seen.add(sha)
        name = ref.removeprefix("refs/heads/").removeprefix("refs/tags/").removesuffix("^{}")
        what = "tag" if ref.startswith("refs/tags/") else "branch"
        main = what == "branch" and sha == head
        out.append({"version": name, "choice": sha, "ref": "main" if main else what,
                    "label": f"{'main branch' if main else what} {name} @ {sha[:10]}"})
    order = {"main": 0, "tag": 1, "branch": 2}
    return sorted(out, key=lambda v: (order[v["ref"]], [(-a, b) for a, b in _vkey(v["version"])]))


def check_all(tools=None):
    """Look up every tool's published versions; saved in tool_updates.json."""
    with _check_lock:
        tools = tools or catalog.load_tools()
        res = {}
        for tid, t in tools.items():
            k = kind(t)
            if not k:
                continue
            try:
                vs = _quay_versions(t) if k == "biocontainers" else _git_versions(t)
                res[tid] = {"kind": k, "versions": vs}
            except Exception as e:  # one tool failing must not stop the others
                res[tid] = {"kind": k, "versions": [], "error": f"{type(e).__name__}: {e}"}
        data = {"checked": dt.datetime.now().isoformat(timespec="seconds"), "tools": res}
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(data, indent=1))
        return data


def status(tools=None):
    """Cached check plus, per tool, what's installed and what's newer."""
    tools = tools or catalog.load_tools()
    try:
        data = json.loads(CACHE.read_text())
    except (OSError, ValueError):
        data = {"checked": None, "tools": {}}
    stale = not data["checked"] or dt.datetime.now() - dt.datetime.fromisoformat(data["checked"]) > DAY
    out = {}
    for tid, t in tools.items():
        info = data["tools"].get(tid) or {}
        have = installed_versions(t)
        newest_have = max((v["version"] for v in have), key=_vkey)
        newer = []
        if info.get("kind") == "biocontainers":
            newer = [v["version"] for v in info.get("versions", []) if _vkey(v["version"]) > _vkey(newest_have)]
        elif info.get("kind") == "git":  # the repo's main branch moved on past every installed commit
            have_commits = {v.get("commit") for v in have}
            newer = [v["label"] for v in info.get("versions", [])
                     if v.get("ref") == "main" and v["choice"] not in have_commits]
        out[tid] = {"kind": kind(t), "installed": have, "available": info.get("versions", []),
                    "newer": newer, "error": info.get("error")}
    return {"checked": data["checked"], "stale": stale, "tools": out,
            "updates": sorted(tid for tid, v in out.items() if v["newer"])}


def check_in_background_if_stale():
    if status()["stale"] and not _check_lock.locked():
        threading.Thread(target=check_all, daemon=True).start()


def installed_versions(t):
    """[{version, default, untested, added, commit}] for one tool, default first."""
    def row(x, default):
        return {"version": str(x.get("version", "")), "default": default, "untested": bool(x.get("untested")),
                "added": x.get("added"), "commit": (x.get("source") or {}).get("commit"),
                "container": x.get("container")}
    rows = [row(t, True)] + [row(x, False) for x in (t.get("_versions") or {}).values()]
    return rows[:1] + sorted(rows[1:], key=lambda r: _vkey(r["version"]), reverse=True)


# ---------------------------------------------------------------- adding and removing

def _quick_check(tool, tdir, log):
    """Start the container and ask the tool its version: proves the image runs."""
    image = tool["container"]
    if not image.startswith("ntg-local/"):
        log(f"downloading container {image}")
        p = subprocess.run(["docker", "pull", "-q", image], capture_output=True, text=True, timeout=1800)
        if p.returncode:
            raise RuntimeError(f"couldn't download {image}: {p.stderr.strip()[-500:]}")
    cmds = tool.get("versions") or {tool["id"]: tool.get("version_command", "true")}
    name, cmd = next(iter(cmds.items()))
    log(f"quick check: running `{cmd}` in the container")
    p = subprocess.run(["docker", "run", "--rm", "-v", f"{tdir}:/tooldir:ro", "-e", "TOOL_DIR=/tooldir",
                        "-e", "HOME=/tmp", image, "sh", "-c", cmd], capture_output=True, text=True, timeout=600)
    out = (p.stdout + p.stderr).strip()
    bad = re.search(r"not found|No such file|Traceback", out)
    if p.returncode or not out or bad:
        raise RuntimeError(f"the new version didn't start correctly:\n{out[-1500:] or '(no output)'}")
    log(f"{name} reports: {out.splitlines()[-1]}")
    return out.splitlines()[-1]


def add_version(tool_id, choice, log=print):
    """choice: a quay.io image (built-in tools) or a commit of the pack's repo (packs)."""
    tools = catalog.load_tools()
    base = tools[tool_id]
    k = kind(base)
    taken = {v["version"] for v in installed_versions(base)}
    today = dt.date.today().isoformat()
    if k == "biocontainers":
        version = choice.rsplit(":", 1)[1].split("--")[0]
        if version in taken:
            raise ValueError(f"{base['name']} {version} is already installed")
        tdir = _versions_dir() / tool_id / _safe(version)
        shutil.rmtree(tdir, ignore_errors=True)
        shutil.copytree(base["_dir"], tdir)
        raw = catalog.load_yaml(Path(base["_dir"]) / "tool.yml")
        t = {**raw, "version": version, "container": choice, "untested": True, "added": today,
             "based_on": str(base.get("version"))}
        catalog.dump_yaml(t, tdir / "tool.yml")
    elif k == "git":
        pack_id = base["source"]["pack"]
        tmp = Path(tempfile.mkdtemp(prefix="ntg-ver-"))
        try:
            log(f"cloning {base['source']['repo']} @ {choice[:12]} and building its container (takes a few minutes)")
            pack, image, commit = toolpacks.fetch_pack(pack_id, choice, tmp / "repo", log)
            tool = next((x for x in pack["tools"] if x["id"] == tool_id), None)
            if not tool:
                raise ValueError(f"commit {commit[:10]} of the pack has no tool {tool_id}")
            version = str(tool.get("version", "")) or commit[:7]
            if version in taken:
                version = f"{version}+{commit[:7]}"
            if version in taken:
                raise ValueError(f"{base['name']} at commit {commit[:10]} is already installed")
            tdir = _versions_dir() / tool_id / _safe(version)
            shutil.rmtree(tdir, ignore_errors=True)
            t = toolpacks.install_tool(pack, tool, tmp / "repo", image, commit, tdir,
                                       extra={"version": version, "untested": True, "added": today,
                                              "based_on": str(base.get("version"))})
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    else:
        raise ValueError(f"{base['name']} is part of the pipeline and has no separate versions")
    try:
        t["check"] = _quick_check(t, tdir, log)
    except Exception:
        shutil.rmtree(tdir, ignore_errors=True)
        raise
    catalog.dump_yaml(t, tdir / "tool.yml")
    log(f"added {base['name']} {version} (untested until a run with it succeeds)")
    return version


def used_by(tool_id, version):
    """User workflows (any saved version) whose steps pick this tool version."""
    out = []
    for d in sorted((catalog.HOME / "workflows").glob("*")):
        for f in catalog.user_workflow_versions(d.name):
            wf = catalog.load_yaml(f)
            if any(s.get("tool") == tool_id and str(s.get("tool_version", "")) == str(version)
                   for s in (wf.get("steps") or {}).values()):
                out.append(f"{wf.get('name', d.name)} v{f.stem[1:]}")
    return out


def remove_version(tool_id, version):
    t = (catalog.load_tools()[tool_id].get("_versions") or {}).get(str(version))
    if not t:
        raise ValueError("only versions added on the Tools page can be removed")
    shutil.rmtree(t["_dir"])


def mark_tested(wf, steps_ran):
    """After a successful run: versions used by steps that ran are no longer untested."""
    tools = catalog.load_tools()
    for sid in steps_ran:
        step = (wf.get("steps") or {}).get(sid)
        t = catalog.step_tool(tools, step) if step else None
        if t and t.get("untested"):
            raw = catalog.load_yaml(Path(t["_dir"]) / "tool.yml")
            raw["untested"] = False
            raw["tested"] = dt.date.today().isoformat()
            catalog.dump_yaml(raw, Path(t["_dir"]) / "tool.yml")
