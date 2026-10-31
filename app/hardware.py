"""Reads this computer's specs and turns tool requirements into plain warnings."""
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

from . import catalog


def _run(*cmd, timeout=10):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr).strip()
    except (OSError, subprocess.TimeoutExpired):
        return 1, ""


def _meminfo():
    out = {}
    try:
        for line in open("/proc/meminfo"):
            k, v = line.split(":", 1)
            out[k] = int(v.split()[0]) / 1048576  # kB -> GB
    except OSError:
        pass
    return out


def _free_gb(path):
    p = Path(path)
    while not p.exists() and p != p.parent:
        p = p.parent
    try:
        return round(shutil.disk_usage(p).free / 1e9, 1)
    except OSError:
        return None


def check():
    mem = _meminfo()
    wsl = "microsoft" in Path("/proc/version").read_text().lower() if Path("/proc/version").exists() else False
    docker_rc, docker_out = _run("docker", "info", "--format", "{{.ServerVersion}}")
    nf_rc, nf_out = _run("nextflow", "-version", timeout=60)
    nf_ver = next((l.split("version")[1].split()[0] for l in nf_out.splitlines() if "version" in l), None) if nf_rc == 0 else None
    java_rc, java_out = _run("java", "-version")
    db_dir = os.environ.get("NTG_DB_DIR", str(catalog.HOME / "databases"))
    return {
        "cpus": os.cpu_count(),
        "memory_gb": round(mem.get("MemTotal", 0), 1),
        "memory_available_gb": round(mem.get("MemAvailable", 0), 1),
        "wsl": wsl,
        "docker": docker_out if docker_rc == 0 else None,
        "docker_error": None if docker_rc == 0 else (docker_out.splitlines()[-1] if docker_out else "docker not found"),
        "apptainer": _run("apptainer", "--version")[1] if shutil.which("apptainer") else None,
        "nextflow": nf_ver,
        "java": java_out.splitlines()[0] if java_rc == 0 and java_out else None,
        "disk_free_gb": {
            "runs": _free_gb(catalog.HOME / "runs"),
            "work": _free_gb(catalog.HOME / "work"),
            "databases": _free_gb(db_dir),
        },
    }


def _cpu_times():
    try:
        v = [int(x) for x in open("/proc/stat").readline().split()[1:]]
    except (OSError, ValueError):
        return None
    return sum(v), v[3] + (v[4] if len(v) > 4 else 0)  # total, idle + iowait


_last_cpu = None


def _cpu_percent():
    """CPU use since the previous call (the top bar polls every few seconds)."""
    global _last_cpu
    now = _cpu_times()
    if now is None:
        return None
    if _last_cpu is None:
        time.sleep(0.25)
        _last_cpu, now = now, _cpu_times()
    (t0, i0), (t1, i1) = _last_cpu, now
    _last_cpu = now
    return round(100 * (1 - (i1 - i0) / (t1 - t0)), 1) if t1 > t0 else 0.0


# WSL only sees the CPU's fixed base clock, so the live clock is read from Windows:
# base MHz x "% Processor Performance" (above 100% = turbo). A PowerShell loop runs
# for a minute at a time and is only restarted while someone is watching the top bar.
_WIN_CLOCK = ("$m=(Get-CimInstance Win32_Processor | Select -First 1).MaxClockSpeed; foreach($i in 1..30){ "
              "$p=(Get-Counter '\\Processor Information(_Total)\\% Processor Performance').CounterSamples[0].CookedValue; "
              "[Console]::Out.WriteLine(\"$m $p\"); Start-Sleep -Seconds 1 }")
_clock = {"ghz": None, "asked": 0.0, "thread": None}
_clock_lock = threading.Lock()


def _windows_clock_loop():
    while time.time() - _clock["asked"] < 30:
        try:
            p = subprocess.Popen(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _WIN_CLOCK],
                                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        except OSError:
            break
        for line in p.stdout:
            try:
                mhz, perf = (float(x) for x in line.split())
                _clock["ghz"] = round(mhz * perf / 100 / 1000, 2)
            except ValueError:
                pass
        if p.wait() != 0 and _clock["ghz"] is None:
            break  # Windows counters not readable: stop trying, fall back to /proc
    with _clock_lock:
        _clock["thread"] = None


def _cpu_ghz(wsl):
    if wsl and shutil.which("powershell.exe"):
        with _clock_lock:
            _clock["asked"] = time.time()
            if _clock["thread"] is None:
                _clock["thread"] = threading.Thread(target=_windows_clock_loop, daemon=True)
                _clock["thread"].start()
        if _clock["ghz"] is not None:
            return _clock["ghz"], "live"
    freqs = []
    for f in Path("/sys/devices/system/cpu").glob("cpu[0-9]*/cpufreq/scaling_cur_freq"):
        try:
            freqs.append(int(f.read_text()) / 1e6)  # kHz -> GHz
        except (OSError, ValueError):
            pass
    if freqs:
        return round(sum(freqs) / len(freqs), 2), "live"
    try:
        mhz = [float(l.split(":")[1]) for l in open("/proc/cpuinfo") if l.startswith("cpu MHz")]
    except (OSError, ValueError):
        mhz = []
    return (round(sum(mhz) / len(mhz) / 1000, 2), "base") if mhz else (None, None)


def usage():
    """Live use of this computer, for the top bar (cheap: no docker/nextflow calls)."""
    mem = _meminfo()
    wsl = "microsoft" in Path("/proc/version").read_text().lower() if Path("/proc/version").exists() else False
    ghz, ghz_kind = _cpu_ghz(wsl)
    total, avail = mem.get("MemTotal", 0), mem.get("MemAvailable", 0)
    return {
        "cpus": os.cpu_count(),
        "cpu_percent": _cpu_percent(),
        "cpu_ghz": ghz,
        "cpu_ghz_kind": ghz_kind,
        "memory_gb": round(total, 1),
        "memory_used_gb": round(total - avail, 1),
        "disk_free_gb": _free_gb(catalog.HOME / "work"),
    }


def warnings(wf, hw=None, tools=None, databases=None, n_samples=None):
    """List of {level: error|warning|info, step, message} for running wf on this computer."""
    hw = hw or check()
    tools = tools or catalog.load_tools()
    databases = databases or catalog.load_databases()
    out = []

    def add(level, msg, step=None):
        out.append({"level": level, "step": step, "message": msg})

    if not hw["docker"] and not hw["apptainer"]:
        add("error", f"No container engine is running ({hw['docker_error']}). Start Docker: "
            "`sudo service docker start` (or `sudo systemctl start docker`).")
    if not hw["nextflow"]:
        add("error", "Nextflow is not installed. Run the installer again (install.sh).")
    if hw["cpus"] and hw["cpus"] < 4:
        add("warning", f"Only {hw['cpus']} CPU threads: runs will be slow.")

    mem = hw["memory_gb"]
    disk_per_sample = 0.0
    try:
        from .compiler import runnable
        will_run = runnable(wf, tools)
    except Exception:  # broken workflow: reported elsewhere; check every enabled step
        will_run = {sid: st.get("enabled", True) for sid, st in (wf.get("steps") or {}).items()}
    for sid, step in (wf.get("steps") or {}).items():
        if not will_run.get(sid, step.get("enabled", True)):
            continue
        tool = tools.get(step["tool"])
        if not tool:
            add("error", f"Tool '{step['tool']}' is not installed.", sid)
            continue
        req = tool.get("requirements") or {}
        label = f"{step.get('label') or tool['name']} ({tool['name']})"
        if req.get("memory_gb", 0) > mem:
            add("error", f"{label} needs at least {req['memory_gb']} GB RAM; this computer has {mem:.0f} GB.", sid)
        elif req.get("recommended_memory_gb", 0) > mem:
            add("warning", f"{label} works best with {req['recommended_memory_gb']} GB RAM; this computer has {mem:.0f} GB. "
                f"{req.get('note', '')}".strip(), sid)
        full = req.get("full_tree_memory_gb")
        if full:
            tree = (step.get("settings") or {}).get("tree", "auto")
            if tree == "full" and mem < full:
                add("error", f"{label}: the full tree needs ~{full} GB RAM; this computer has {mem:.0f} GB. "
                    "Set Reference tree to Auto or Reduced.", sid)
            elif tree == "auto" and mem < full:
                add("info", f"{label}: this computer has {mem:.0f} GB RAM (< {full} GB), so Auto uses the reduced tree. "
                    "Galaxy used the full tree; completeness/contamination can differ slightly.", sid)
        for d in tool.get("databases") or []:
            db = databases.get(d)
            if not db or not db["installed"]:
                name = db["name"] if db else d
                size = f" ({db['size_gb']} GB download)" if db else ""
                add("error", f"{label} needs the {name} database, which is not installed{size}. "
                    "Install it on the Databases page.", sid)
        disk_per_sample += req.get("disk_gb_per_sample", 0.2)

    if n_samples:
        need = round(disk_per_sample * n_samples * 1.5, 1)
        free = hw["disk_free_gb"].get("work")
        if free is not None and free < need:
            add("error", f"About {need} GB of free disk is needed for {n_samples} samples; only {free} GB is free.")
        elif free is not None and free < need * 3:
            add("warning", f"Disk space is getting low: {free} GB free, a run of {n_samples} samples needs about {need} GB.")
    if hw["wsl"] and mem < 32:
        add("info", f"WSL is using {mem:.0f} GB RAM. Windows gives WSL half its memory by default; you can raise it "
            "with memory=... in %UserProfile%\\.wslconfig (then `wsl --shutdown`).")
    return out
