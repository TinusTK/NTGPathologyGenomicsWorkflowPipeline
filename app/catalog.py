"""Loads tools, workflows and databases from disk.

Layout (all paths relative to the repo root unless noted):
  tools/<id>/tool.yml              built-in tools
  workflows/<id>/workflow.yml      built-in workflows (always locked)
  databases/catalog.yml            reference databases the tools can use
  $NTG_HOME/tools/<repo>/<id>/     tools installed from git repos
  $NTG_HOME/workflows/<id>/v<N>.yml  user workflows, one file per saved version
"""
import os
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
HOME = Path(os.environ.get("NTG_HOME", Path.home() / ".ntg"))

ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def load_yaml(path):
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def dump_yaml(data, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, sort_keys=False, allow_unicode=True, width=100)


# ---------------------------------------------------------------- tools

def tool_dirs():
    """Every folder holding a tool.yml: built-in first, then installed."""
    yield from sorted((ROOT / "tools").glob("*/tool.yml"))
    yield from sorted((HOME / "tools").glob("*/*/tool.yml"))


def load_tools():
    tools = {}
    for f in tool_dirs():
        t = load_yaml(f)
        t["_dir"] = str(f.parent)
        t["_source"] = "built-in" if f.is_relative_to(ROOT) else f.parent.parent.name
        tools.setdefault(t["id"], t)  # built-in wins over an installed tool of the same id
    return tools


# ---------------------------------------------------------------- databases

def load_databases():
    cat = load_yaml(ROOT / "databases" / "catalog.yml").get("databases", {})
    root = Path(os.environ.get("NTG_DB_DIR", HOME / "databases"))
    for db_id, db in cat.items():
        db["id"] = db_id
        db["path"] = str(root / db["install_dir"])
        db["installed"] = (root / db["install_dir"] / db.get("check_file", "")).exists()
    return cat


# ---------------------------------------------------------------- workflows

def _builtin_raw():
    return {(wf := load_yaml(f))["id"]: (wf, f) for f in sorted((ROOT / "workflows").glob("*/workflow.yml"))}


def builtin_workflows():
    raw = _builtin_raw()

    def expand(wf):
        # extends: <id> = the parent's steps, then this workflow's own on top
        if wf.get("extends"):
            parent = expand(dict(raw[wf["extends"]][0]))
            wf = {**parent, **{k: v for k, v in wf.items() if k != "steps"},
                  "steps": {**parent["steps"], **(wf.get("steps") or {})}}
        return wf

    for wf_id, (wf, f) in raw.items():
        wf = expand(dict(wf))
        wf.update(_file=str(f), _version=wf.get("version", 1), locked=True, builtin=True)
        yield wf


def user_workflow_versions(wf_id):
    d = HOME / "workflows" / wf_id
    files = sorted(d.glob("v*.yml"), key=lambda p: int(p.stem[1:]))
    return files


def list_workflows():
    out = []
    for wf in builtin_workflows():
        out.append(_summary(wf, [wf["_version"]]))
    for d in sorted((HOME / "workflows").glob("*")):
        versions = user_workflow_versions(d.name)
        if versions:
            wf = load_yaml(versions[-1])
            out.append(_summary(wf, [int(p.stem[1:]) for p in versions], builtin=False))
    return out


def _summary(wf, versions, builtin=True):
    return {"id": wf["id"], "name": wf["name"], "description": wf.get("description", ""),
            "builtin": builtin, "versions": versions, "latest": versions[-1]}


def load_workflow(wf_id, version=None):
    for wf in builtin_workflows():
        if wf["id"] == wf_id:
            return wf
    versions = user_workflow_versions(wf_id)
    if not versions:
        raise KeyError(f"unknown workflow {wf_id}")
    f = versions[-1] if version is None else HOME / "workflows" / wf_id / f"v{int(version)}.yml"
    wf = load_yaml(f)
    wf.update(_file=str(f), _version=int(Path(f).stem[1:]), locked=True, builtin=False)
    return wf


def save_workflow(wf, new_id=None):
    """Save as a new version; never overwrites. Built-ins are saved as a new user workflow."""
    wf = {k: v for k, v in wf.items() if not k.startswith("_") and k not in ("locked", "builtin")}
    if new_id:
        if not ID_RE.match(new_id):
            raise ValueError("workflow id: lowercase letters, digits and _ only, starting with a letter")
        wf["id"] = new_id
    if any(b["id"] == wf["id"] for b in builtin_workflows()):
        raise ValueError("built-in workflows are read-only: save it under a new id")
    existing = user_workflow_versions(wf["id"])
    version = int(existing[-1].stem[1:]) + 1 if existing else 1
    wf["version"] = version
    dump_yaml(wf, HOME / "workflows" / wf["id"] / f"v{version}.yml")
    return version
