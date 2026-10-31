"""Tool packs: tools that live in other git repositories.

A pack is a YAML file (packs/<id>.yml in this repo, or ntg/pack.yml inside the
tool's own repo) giving the repo, the commit to pin, how to get the container,
and one or more tool definitions in the tool.yml format. `files` in a tool
are paths inside the repo (files or folders); they are copied into the
installed tool's folder, so a run never depends on the clone again.

Installing: clone at the pinned commit -> build/pull the container -> write
$NTG_HOME/tools/<pack>/<tool>/tool.yml (+ files) -> the tool shows up in the
tool list and can be added to any workflow.
"""
import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import catalog

MICROMAMBA = "mambaorg/micromamba:2.0.5"


def _packs_dir():
    return catalog.ROOT / "packs"


def list_packs():
    out = []
    for f in sorted(_packs_dir().glob("*.yml")):
        p = catalog.load_yaml(f)
        out.append({"id": p["id"], "name": p["name"], "repo": p["repo"], "commit": p.get("commit"),
                    "description": p.get("description", ""), "tools": [t["id"] for t in p["tools"]],
                    "installed": (catalog.HOME / "tools" / p["id"] / "pack.yml").exists()})
    return out


def _git(*args, cwd=None):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _clone(repo, commit, dest):
    dest.mkdir(parents=True)
    _git("init", "-q", cwd=dest)
    _git("remote", "add", "origin", repo, cwd=dest)
    _git("fetch", "-q", "--depth", "1", "origin", commit or "HEAD", cwd=dest)
    _git("checkout", "-q", "FETCH_HEAD", cwd=dest)
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=dest, capture_output=True, text=True).stdout.strip()


def _build_image(pack, repo_dir, log):
    """Container from a conda environment (inline, or a file in the repo), built locally with micromamba."""
    spec = pack["container"]["build"]
    if "environment_file" in spec:
        env_text = (repo_dir / spec["environment_file"]).read_text()
    else:
        env_text = json.dumps({"name": "base", "channels": spec["channels"], "dependencies": spec["dependencies"]})
    # procps gives `ps`, which Nextflow needs inside every task container to collect task metrics.
    dockerfile = (
        f"FROM {MICROMAMBA}\n"
        "COPY --chown=$MAMBA_USER:$MAMBA_USER environment.yml /tmp/environment.yml\n"
        "RUN micromamba install -y -n base -f /tmp/environment.yml && \\\n"
        "    micromamba env export -n base > /opt/conda/ntg-environment.lock.yml && \\\n"
        "    micromamba clean --all --yes\n"
        "ENV PATH=/opt/conda/bin:$PATH\n"
        "USER root\n"
        "RUN apt-get update && apt-get install -y --no-install-recommends procps && \\\n"
        "    rm -rf /var/lib/apt/lists/*\n")
    tag = f"ntg-local/{pack['id'].replace('_', '-')}:{hashlib.sha256((dockerfile + env_text).encode()).hexdigest()[:12]}"
    if subprocess.run(["docker", "image", "inspect", tag], capture_output=True).returncode == 0:
        log(f"container {tag} already built")
        return tag
    with tempfile.TemporaryDirectory() as d:
        Path(d, "environment.yml").write_text(env_text)
        Path(d, "Dockerfile").write_text(dockerfile)
        log(f"building container {tag} (conda environment; takes a few minutes)")
        p = subprocess.run(["docker", "build", "-q", "-t", tag, d], capture_output=True, text=True)
        if p.returncode != 0:
            raise RuntimeError(f"container build failed:\n{p.stderr[-3000:]}")
    return tag


def install(source, log=print):
    """source: a pack id from packs/, or a git URL whose repo has ntg/pack.yml."""
    pack_file = _packs_dir() / f"{source}.yml"
    tmp = Path(tempfile.mkdtemp(prefix="ntg-pack-"))
    try:
        if pack_file.exists():
            pack = catalog.load_yaml(pack_file)
            log(f"cloning {pack['repo']} @ {pack.get('commit', 'HEAD')[:12]}")
            commit = _clone(pack["repo"], pack.get("commit"), tmp / "repo")
        else:
            log(f"cloning {source}")
            commit = _clone(source, None, tmp / "repo")
            f = tmp / "repo" / "ntg" / "pack.yml"
            if not f.exists():
                raise ValueError("that repo has no ntg/pack.yml, so it can't be installed as a tool pack")
            pack = catalog.load_yaml(f)
            pack["repo"] = source
        pack["commit"] = commit
        repo_dir = tmp / "repo"

        c = pack.get("container") or {}
        image = c.get("image") or _build_image(pack, repo_dir, log)

        dest = catalog.HOME / "tools" / pack["id"]
        shutil.rmtree(dest, ignore_errors=True)
        for tool in pack["tools"]:
            tdir = dest / tool["id"]
            tdir.mkdir(parents=True)
            names = []
            for rel in tool.get("files") or []:
                src = repo_dir / rel
                target = tdir / src.name
                (shutil.copytree if src.is_dir() else shutil.copy2)(src, target)
                names.append(src.name)
            t = {**tool, "files": names, "container": tool.get("container", image),
                 "source": {"pack": pack["id"], "repo": pack["repo"], "commit": commit}}
            catalog.dump_yaml(t, tdir / "tool.yml")
            log(f"installed tool {tool['id']} ({tool['name']} {tool.get('version', '')})")
        catalog.dump_yaml({k: v for k, v in pack.items() if k != "tools"}, dest / "pack.yml")
        return pack
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def uninstall(pack_id):
    shutil.rmtree(catalog.HOME / "tools" / pack_id, ignore_errors=True)
