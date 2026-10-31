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
import csv
import datetime as dt
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


def natural(s):
    """Alphabetical sort key that ignores case and puts sample2 before sample10."""
    return [(0, int(p), "") if p.isdigit() else (1, 0, p.lower()) for p in re.split(r"(\d+)", str(s)) if p]


READS_RE = re.compile(r"\.(fastq|fq)(\.gz)?$|\.gz$", re.I)
# Tried in this order by Auto; the one that pairs the most files wins.
PAIRINGS = [("_R1", "_R2"), (".R1", ".R2"), ("_1", "_2"), (".1", ".2")]


def _pair(names, r1, r2):
    """Pair file names on markers r1/r2: sample = text before the last r1 that isn't
    followed by another digit (so _R1 matches _R1_001 but not _R10)."""
    rx = re.compile(re.escape(r1) + r"(?!\d)")
    have, pairs, used = set(names), [], set()
    for n in names:
        m = None
        for m in rx.finditer(n):
            pass
        if not m:
            continue
        partner = n[: m.start()] + r2 + n[m.end():]
        sample = n[: m.start()].rstrip("_.-") or n[: m.start()]
        pairs.append({"sample": sample, "r1": n, "r2": partner if partner in have else None})
        used.add(n)
        if partner in have:
            used.add(partner)
    return pairs, used


def find_samples(input_dir, pairing=None):
    """Pair the reads in input_dir. pairing: {mode: auto|custom, r1, r2}.
    Returns {samples: [{sample, r1, r2}], unpaired: [names], r1, r2, mode}: what
    the Run page shows, and exactly what goes into the run's samples.csv."""
    pairing = pairing or {}
    d = Path(input_dir).expanduser()
    names = sorted((f.name for f in d.iterdir() if f.is_file() and READS_RE.search(f.name)), key=natural) if d.is_dir() else []
    if pairing.get("mode") == "custom" and pairing.get("r1") and pairing.get("r2"):
        tries = [(pairing["r1"], pairing["r2"])]
    else:
        tries = PAIRINGS
    best = None
    for r1, r2 in tries:
        pairs, used = _pair(names, r1, r2)
        score = sum(1 for p in pairs if p["r2"])
        if best is None or score > best[0]:
            best = (score, r1, r2, pairs, used)
    _, r1, r2, pairs, used = best
    pairs.sort(key=lambda p: natural(p["sample"]))
    for p in pairs:
        p["r1"] = str(d / p["r1"])
        p["r2"] = str(d / p["r2"]) if p["r2"] else None
    return {"samples": pairs, "unpaired": [n for n in names if n not in used],
            "r1": r1, "r2": r2, "mode": "custom" if len(tries) == 1 else "auto"}


def pairing_problems(found):
    """Plain error messages for pairs that can't run."""
    out, seen = [], {}
    for s in found["samples"]:
        if not s["r2"]:
            out.append(f"{s['sample']}: {Path(s['r1']).name} has no matching {found['r2']} file.")
        seen.setdefault(s["sample"], []).append(s)
    out += [f"Sample name {k} is used by {len(v)} pairs: rename the files or change the pairing."
            for k, v in seen.items() if len(v) > 1]
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


def start(wf_id, version, input_dir, name=None, cleanup=True, steps_off=(), extra=None, wait=False, pairing=None):
    wf = catalog.load_workflow(wf_id, version)
    input_dir = str(Path(input_dir).expanduser().resolve())
    found = find_samples(input_dir, pairing)
    samples = found["samples"]
    if not samples:
        raise ValueError(f"no paired reads found in {input_dir}")
    problems = pairing_problems(found)
    if problems:
        raise ValueError(" ".join(problems))
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    run_id = f"{stamp}_{re.sub(r'[^A-Za-z0-9_-]+', '-', name or wf_id)[:40]}"
    rdir = RUNS / run_id
    compiler.write_bundle(wf, rdir)
    steps_off = [s for s in steps_off if s in wf["steps"]]
    run = {
        "id": run_id, "name": name or wf["name"], "workflow": {"id": wf["id"], "version": wf["_version"], "name": wf["name"]},
        "input": input_dir, "samples": [s["sample"] for s in samples], "n_samples": len(samples),
        "pairing": {"mode": found["mode"], "r1": found["r1"], "r2": found["r2"], "unpaired": found["unpaired"]},
        "cleanup": bool(cleanup), "steps_off": steps_off, "status": "starting",
        "started": dt.datetime.now().isoformat(timespec="seconds"), "finished": None,
        "results": str(rdir / "results"), "work": str(WORK / run_id), "steps": {}, "error": None,
    }
    save(run)
    # The pairs exactly as shown on the Run page; Nextflow reads them from here.
    with open(rdir / "samples.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["sample", "r1", "r2"])
        w.writerows([s["sample"], s["r1"], s["r2"]] for s in samples)
    cmd = ["nextflow", "run", "main.nf", "-profile", _profile(), "-w", run["work"],
           "--input", input_dir, "--samplesheet", str(rdir / "samples.csv"),
           "--outdir", "results", "--cleanup", str(bool(cleanup)).lower()]
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
    if rc == 0:  # Nextflow succeeded, so every step that was set to run did run
        try:
            from . import toolversions
            wf = catalog.load_yaml(rdir / "workflow.yml")
            for s in run.get("steps_off") or []:
                if s in wf.get("steps", {}):
                    wf["steps"][s]["enabled"] = False
            ran = [sid for sid, ok in compiler.runnable(wf, catalog.load_tools()).items() if ok]
            toolversions.mark_tested(wf, ran)
        except Exception:  # never let bookkeeping fail a finished run
            pass
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
        # events arrive from two sources (weblog, console) in any order: never go backwards
        rank = {"SUBMITTED": 1, "RUNNING": 2, "COMPLETED": 3, "CACHED": 3, "FAILED": 3, "ABORTED": 3}
        if rank.get(status, 3) < rank.get(cur.get("status"), 0):
            return
        if status == "RUNNING" and "started_at" not in cur:
            cur["started_at"] = dt.datetime.now().isoformat(timespec="seconds")
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
