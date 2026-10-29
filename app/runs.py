"""Starting runs and following their progress.

Each run gets $NTG_HOME/runs/<run_id>/:
  main.nf, params.config, nextflow.config, lib/, tools/, workflow.yml
      the compiled workflow exactly as it ran (never shared between runs)
  run.json         status, options, per-step progress
  nextflow.log     Nextflow's console output
  results/         the outputs (samples/, combined/, summary/, pipeline_info/)
Nextflow's work folder goes to $NTG_HOME/work/<run_id> (on the Linux disk:
much faster than a Windows drive under WSL) and is deleted after a
successful run when cleanup is on.
"""
import datetime as dt
import glob
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path

from . import catalog, compiler

RUNS = catalog.HOME / "runs"
WORK = catalog.HOME / "work"
_lock = threading.Lock()
WEBLOG_URL = None  # set by the server so Nextflow can post progress events


def find_samples(input_dir, pattern="*_R{1,2}*.gz"):
    """Same pairing as Nextflow's fromFilePairs: sample = text before _R1/_R2."""
    pre, rest = pattern.split("{1,2}", 1)
    lit = pre.lstrip("*")
    out = []
    for f in sorted(glob.glob(str(Path(input_dir) / (pre + "1" + rest)))):
        name = Path(f).name
        sample = name[: name.rfind(lit + "1")]
        r2 = sorted(glob.glob(str(Path(input_dir) / (glob.escape(sample) + lit + "2" + rest))))
        out.append({"sample": sample, "r1": f, "r2": r2[0] if r2 else None})
    return out


def _path(run_id):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", run_id):
        raise ValueError("bad run id")
    return RUNS / run_id


def load(run_id):
    return json.loads((_path(run_id) / "run.json").read_text())


def save(run):
    with _lock:
        f = _path(run["id"]) / "run.json"
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(run, indent=2))
        tmp.replace(f)


def list_runs():
    out = []
    for f in sorted(RUNS.glob("*/run.json"), reverse=True):
        try:
            r = json.loads(f.read_text())
            if r.get("status") in ("running", "starting", "stopped") and not r.get("finished"):
                r = refresh(r["id"])
        except ValueError:
            continue
        out.append({k: r.get(k) for k in ("id", "name", "workflow", "status", "started", "finished", "n_samples")})
    return out


def start(wf_id, version, input_dir, name=None, cleanup=True, steps_off=(), extra=None, wait=False):
    wf = catalog.load_workflow(wf_id, version)
    input_dir = str(Path(input_dir).expanduser().resolve())
    samples = find_samples(input_dir, (wf.get("input") or {}).get("pattern", "*_R{1,2}*.gz"))
    if not samples:
        raise ValueError(f"no paired reads found in {input_dir}")
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    run_id = f"{stamp}_{re.sub(r'[^A-Za-z0-9_-]+', '-', name or wf_id)[:40]}"
    rdir = RUNS / run_id
    compiler.write_bundle(wf, rdir)
    steps_off = [s for s in steps_off if s in wf["steps"]]
    run = {
        "id": run_id, "name": name or wf["name"], "workflow": {"id": wf["id"], "version": wf["_version"], "name": wf["name"]},
        "input": input_dir, "samples": [s["sample"] for s in samples], "n_samples": len(samples),
        "cleanup": bool(cleanup), "steps_off": steps_off, "status": "starting",
        "started": dt.datetime.now().isoformat(timespec="seconds"), "finished": None,
        "results": str(rdir / "results"), "work": str(WORK / run_id), "steps": {}, "error": None,
    }
    save(run)
    cmd = ["nextflow", "run", "main.nf", "-profile", _profile(), "-w", run["work"],
           "--input", input_dir, "--outdir", "results", "--cleanup", str(bool(cleanup)).lower()]
    for s in steps_off:
        cmd += [f"--steps.{s}", "false"]
    if WEBLOG_URL:
        with open(rdir / "nextflow.config", "a") as fh:
            fh.write(f"weblog {{ enabled = true; url = '{WEBLOG_URL}/{run_id}' }}\n")
    cmd += list(extra or [])
    run["command"] = shlex.join(cmd)
    # Detached from the UI: closing the UI window or restarting it never stops a
    # run. Nextflow writes its own log; the exit code lands in .exitcode.
    p = subprocess.Popen(["bash", "-c", f"{run['command']} > nextflow.log 2>&1; echo $? > .exitcode"],
                         cwd=rdir, start_new_session=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         env={**os.environ, "NXF_ANSI_LOG": "false", "NXF_DISABLE_CHECK_LATEST": "true"})
    run["status"] = "running"
    run["pid"] = p.pid
    save(run)
    if wait:
        _follow(run_id, echo=True)
        return load(run_id)
    threading.Thread(target=_follow, args=(run_id,), daemon=True).start()
    return run


def _profile():
    return "docker" if shutil.which("docker") else "apptainer"


def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except (OSError, TypeError):
        return False


def _follow(run_id, echo=False):
    """Watch the log until the run ends (console progress, optional echo), then finish it."""
    rdir = _path(run_id)
    log, pos = rdir / "nextflow.log", 0
    while True:
        done = (rdir / ".exitcode").exists()
        if log.exists():
            with open(log, errors="replace") as fh:
                fh.seek(pos)
                for line in fh:
                    if echo:
                        print(line, end="", flush=True)
                    _from_console(run_id, line)
                pos = fh.tell()
        if done or not _alive(load(run_id).get("pid")):
            break
        time.sleep(2)
    refresh(run_id)


def refresh(run_id):
    """Finish a run whose Nextflow has exited (also after a UI restart)."""
    run = load(run_id)
    if run["status"] not in ("running", "starting", "stopped") or run.get("finished"):
        return run
    rdir = _path(run_id)
    code_file = rdir / ".exitcode"
    if code_file.exists():
        rc = int(code_file.read_text().strip() or 1)
    elif _alive(run.get("pid")):
        return run
    else:
        rc = None  # process gone without an exit code: killed
    if run["status"] != "stopped":
        run["status"] = "succeeded" if rc == 0 else ("failed" if rc is not None else "interrupted")
    run["exit_code"] = rc
    run["finished"] = dt.datetime.now().isoformat(timespec="seconds")
    if rc != 0 and not run.get("error"):
        log = rdir / "nextflow.log"
        tail = log.read_text(errors="replace").splitlines()[-40:] if log.exists() else []
        run["error"] = "\n".join(tail)
    save(run)
    if rc == 0 and run["cleanup"]:
        shutil.rmtree(run["work"], ignore_errors=True)
    return run


ANSI = re.compile(r"\x1b(\[[0-9;]*[A-Za-z]|\([A-Z0-9])")
_SUBMIT = re.compile(r"Submitted process > (STEP_\w+)(?: \((.*)\))?")


def _from_console(run_id, line):
    """Fallback progress from console lines, when weblog events are not coming in."""
    m = _SUBMIT.search(line)
    if m:
        _task_event(run_id, m.group(1), m.group(2) or "", "SUBMITTED")


def _task_event(run_id, process, tag, status, extra=None):
    step = process.split(":")[-1].removeprefix("STEP_")
    with _lock:
        f = _path(run_id) / "run.json"
        run = json.loads(f.read_text())
        st = run["steps"].setdefault(step, {"tasks": {}})
        key = tag or "all"
        cur = st["tasks"].get(key, {})
        order = ["SUBMITTED", "RUNNING", "COMPLETED", "FAILED", "ABORTED", "CACHED"]
        if cur.get("status") in ("COMPLETED", "FAILED", "CACHED") and status in ("SUBMITTED", "RUNNING"):
            return
        cur.update({"status": status, **(extra or {})})
        st["tasks"][key] = cur
        f.write_text(json.dumps(run, indent=2))


def weblog(run_id, event):
    """Handle one Nextflow weblog event."""
    kind = event.get("event", "")
    trace = event.get("trace") or {}
    if kind.startswith("process_") and trace.get("process"):
        status = {"process_submitted": "SUBMITTED", "process_started": "RUNNING"}.get(kind, trace.get("status", "COMPLETED"))
        extra = {k: trace.get(k) for k in ("exit", "duration", "realtime", "peak_rss", "%cpu", "workdir") if trace.get(k) is not None}
        _task_event(run_id, trace["process"], trace.get("tag") or "", status, extra)
    elif kind == "error":
        run = load(run_id)
        run["error"] = (event.get("metadata") or {}).get("workflow", {}).get("errorMessage") or run.get("error")
        save(run)


def stop(run_id):
    run = load(run_id)
    if run.get("finished") or not _alive(run.get("pid")):
        return False
    run["status"] = "stopped"
    save(run)
    try:
        os.killpg(run["pid"], signal.SIGTERM)  # Nextflow stops its tasks cleanly on TERM
    except OSError:
        return False
    return True


def detail(run_id):
    run = refresh(run_id)
    rdir = _path(run_id)
    log = rdir / "nextflow.log"
    run["log_tail"] = [ANSI.sub("", l) for l in log.read_text(errors="replace").splitlines()[-60:]] if log.exists() else []
    if run.get("error"):
        run["error"] = ANSI.sub("", run["error"])
    res = rdir / "results"
    run["files"] = sorted(str(p.relative_to(res)) for p in res.rglob("*") if p.is_file()) if res.exists() else []
    prov = res / "pipeline_info" / "provenance.json"
    run["provenance"] = json.loads(prov.read_text()) if prov.exists() else None
    return run


def result_file(run_id, rel):
    res = (_path(run_id) / "results").resolve()
    f = (res / rel).resolve()
    if not f.is_relative_to(res) or not f.is_file():
        raise FileNotFoundError(rel)
    return f


def delete(run_id):
    run = load(run_id)
    if run["status"] in ("running", "starting"):
        raise ValueError("stop the run first")
    shutil.rmtree(run["work"], ignore_errors=True)
    shutil.rmtree(_path(run_id), ignore_errors=True)
