"""Updates: compare this pipeline folder with its git remote and fast-forward to it.

Only the pipeline folder changes. Saved workflows, runs, databases and tool packs
live in $NTG_HOME (~/.ntg), so an update never touches them.
"""
import os
import subprocess

from . import catalog, runs

ROOT = catalog.ROOT


def _git(*args, timeout=30):
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}  # never wait for a password
    try:
        p = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, timeout=timeout, env=env)
    except FileNotFoundError:
        return 1, "git is not installed"
    except subprocess.TimeoutExpired:
        return 1, "git timed out (no internet?)"
    return p.returncode, (p.stdout if p.returncode == 0 else p.stderr or p.stdout).strip()


def _commit(ref):
    rc, out = _git("log", "-1", "--format=%h%x09%ad%x09%s", "--date=short", ref)
    if rc:
        return None
    sha, date, subject = out.split("\t", 2)
    return {"commit": sha, "date": date, "subject": subject}


def _running():
    return [r["name"] or r["id"] for r in runs.list_runs() if r["status"] in ("running", "starting")]


def status(fetch=True):
    """What this copy is, and whether the remote has something newer."""
    rc, branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    if rc:
        return {"error": f"This pipeline folder is not a git clone, so it can't update itself ({branch})."}
    current = _commit("HEAD")
    rc, upstream = _git("rev-parse", "--abbrev-ref", "@{u}")
    if rc:
        return {"current": current, "branch": branch,
                "error": f"Branch '{branch}' doesn't follow a branch on GitHub, so there is nothing to compare with."}
    out = {"current": current, "branch": branch, "upstream": upstream, "checked": False}
    if fetch:
        remote = upstream.split("/", 1)[0]
        rc, msg = _git("fetch", "--quiet", remote, timeout=60)
        if rc:
            out["error"] = f"Couldn't reach GitHub to check for updates: {msg.splitlines()[-1] if msg else 'unknown error'}"
            return out
        out["checked"] = True
    rc, log = _git("log", "--format=%h%x09%ad%x09%s", "--date=short", f"HEAD..{upstream}")
    out["new"] = [dict(zip(("commit", "date", "subject"), line.split("\t", 2))) for line in log.splitlines() if line] if not rc else []
    rc, ahead = _git("rev-list", "--count", f"{upstream}..HEAD")
    out["ahead"] = int(ahead) if not rc and ahead.isdigit() else 0
    rc, dirty = _git("status", "--porcelain", "--untracked-files=no")
    out["changed_files"] = [line[3:] for line in dirty.splitlines()] if not rc else []
    out["latest"] = _commit(upstream) if out["new"] else current
    out["blocked"] = _blocked(out)
    return out


def _blocked(st):
    if st["changed_files"]:
        return "Files in the pipeline folder have been edited here, and updating could overwrite them: " + ", ".join(st["changed_files"])
    if st["ahead"]:
        return f"This copy has {st['ahead']} change(s) that aren't on GitHub, so it can't simply be moved to the new version."
    names = _running()
    if names:
        return "A run is going (" + ", ".join(names) + "). Update when it has finished."
    return None


def update():
    """Fast-forward to the remote. Refuses when that could lose work or disturb a run."""
    st = status(fetch=True)
    if st.get("error"):
        raise ValueError(st["error"])
    if not st["new"]:
        return {"updated": False, "current": st["current"]}
    if st["blocked"]:
        raise ValueError(st["blocked"])
    rc, msg = _git("merge", "--ff-only", "--quiet", st["upstream"], timeout=120)
    if rc:
        raise ValueError(f"Update failed: {msg}")
    return {"updated": True, "current": _commit("HEAD"), "applied": st["new"]}
