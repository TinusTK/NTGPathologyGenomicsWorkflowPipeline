"""Local web UI: a small JSON API plus the static page in app/static/.

Standard library only (http.server). Listens on 127.0.0.1 by default, so only
this computer can reach it.
"""
import json
import mimetypes
import os
import sys
import threading
import traceback
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import catalog, compiler, databases, hardware, runs, toolpacks, toolversions, updates

STATIC = Path(__file__).parent / "static"
JOBS = {}


def _job(title, fn):
    """Run fn(log, progress) in the background; the UI polls /api/jobs/<id>."""
    jid = uuid.uuid4().hex[:8]
    job = {"id": jid, "title": title, "status": "running", "log": [], "progress": None}
    JOBS[jid] = job

    def log(msg):
        job["log"].append(str(msg))

    def progress(done, total):
        job["progress"] = [done, total]

    def go():
        try:
            fn(log, progress)
            job["status"] = "done"
        except Exception as e:  # shown to the user in the UI
            job["log"].append(f"ERROR: {e}")
            job["status"] = "failed"

    threading.Thread(target=go, daemon=True).start()
    return job


def _public_tool(t, base=None):
    """Tool for the UI; versions = every installed version of the tool (base: the default one)."""
    out = {k: v for k, v in t.items() if k not in ("_dir", "_versions")}
    out["versions"] = toolversions.installed_versions(base or t)
    out["version_kind"] = toolversions.kind(base or t)
    return out


def workflow_view(wf_id, version=None):
    """Workflow with every step's tool, resolved settings and compile errors, for the UI."""
    wf = catalog.load_workflow(wf_id, version)
    tools = catalog.load_tools()
    versions = ([wf["_version"]] if wf.get("builtin")
                else [int(p.stem[1:]) for p in catalog.user_workflow_versions(wf_id)])
    error = None
    try:
        compiler.resolve(wf, tools)
    except compiler.CompileError as e:
        error = str(e)
    steps = {}
    for sid, step in wf["steps"].items():
        tool = catalog.step_tool(tools, step)
        settings = {}
        if tool:
            for name, spec in (tool.get("settings") or {}).items():
                settings[name] = {**spec, "value": (step.get("settings") or {}).get(name, spec.get("default"))}
        steps[sid] = {**step, "tool_info": _public_tool(tool, tools[step["tool"]]) if tool else None, "resolved_settings": settings}
    return {**{k: v for k, v in wf.items() if k != "steps"}, "steps": steps, "versions": versions, "error": error}


class Handler(BaseHTTPRequestHandler):
    server_version = "ntg"

    def log_message(self, fmt, *args):  # keep the console quiet
        pass

    # ------------------------------------------------------------ helpers
    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def _file(self, path, download=False):
        data = Path(path).read_bytes()
        ctype = mimetypes.guess_type(str(path))[0] or "text/plain"
        if Path(path).suffix in (".tsv", ".tab", ".log", ".txt", ".yml", ".csv", ".out", ".nf", ".config"):
            ctype = "text/plain; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        if download:
            self.send_header("Content-Disposition", f'attachment; filename="{Path(path).name}"')
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def do_DELETE(self):
        self._route("DELETE")

    # ------------------------------------------------------------ routes
    def _route(self, method):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        parts = [unquote(p) for p in u.path.strip("/").split("/") if p]
        try:
            if not parts or parts[0] != "api":
                return self._static(u.path)
            self._api(method, parts[1:], q)
        except (KeyError, FileNotFoundError) as e:
            self._send(404, {"error": f"not found: {e}"})
        except (ValueError, compiler.CompileError) as e:
            self._send(400, {"error": str(e)})
        except Exception as e:
            traceback.print_exc()
            self._send(500, {"error": f"{type(e).__name__}: {e}"})

    def _static(self, path):
        rel = path.strip("/") or "index.html"
        f = (STATIC / rel).resolve()
        if not f.is_relative_to(STATIC.resolve()) or not f.is_file():
            f = STATIC / "index.html"
        self._file(f)

    def _api(self, method, p, q):
        g, post, delete = method == "GET", method == "POST", method == "DELETE"
        if g and p == ["hardware"]:
            return self._send(200, hardware.check())
        if g and p == ["usage"]:
            return self._send(200, hardware.usage())
        if g and p == ["updates"]:
            return self._send(200, updates.status(fetch=q.get("fetch") != "0"))
        if post and p == ["updates", "install"]:
            r = updates.update()
            self._send(200, r)
            if r["updated"]:  # load the new code: restart once the answer has gone out
                threading.Timer(1.0, _restart).start()
            return
        if g and p == ["tools"]:
            return self._send(200, [{**_public_tool(t), "variants": {v: _public_tool(x, t) for v, x in (t.get("_versions") or {}).items()}}
                                    for t in catalog.load_tools().values()])
        if g and p == ["tools", "updates"]:
            toolversions.check_in_background_if_stale()  # once a day, quietly
            return self._send(200, toolversions.status())
        if post and p == ["tools", "updates", "check"]:
            return self._send(200, _job("Check for tool updates", lambda log, prog: (
                toolversions.check_all(), log(f"{len(toolversions.status()['updates'])} tool(s) have a newer version"))))
        if post and p == ["tools", "versions", "add"]:
            b = self._body()
            return self._send(200, _job(f"Add {b['tool']} version", lambda log, prog: toolversions.add_version(b["tool"], b["choice"], log)))
        if g and p == ["tools", "versions", "used"]:
            return self._send(200, toolversions.used_by(q["tool"], q["version"]))
        if post and p == ["tools", "versions", "remove"]:
            b = self._body()
            toolversions.remove_version(b["tool"], b["version"])
            return self._send(200, {"ok": True})
        if g and p == ["workflows"]:
            return self._send(200, catalog.list_workflows())
        if delete and len(p) == 2 and p[0] == "workflows":
            catalog.delete_workflow(p[1])
            return self._send(200, {"ok": True})
        if g and len(p) == 2 and p[0] == "workflows":
            return self._send(200, workflow_view(p[1], q.get("version")))
        if post and p == ["workflows", "save"]:
            b = self._body()
            wf = b["workflow"]
            compiler.resolve(wf, catalog.load_tools())  # refuse to save a broken workflow
            version = catalog.save_workflow(wf, b.get("new_id"))
            return self._send(200, {"id": b.get("new_id") or wf["id"], "version": version})
        if post and p == ["workflows", "check"]:
            b = self._body()
            wf = b["workflow"]
            tools = catalog.load_tools()
            error = None
            try:
                compiler.resolve(wf, tools)
            except compiler.CompileError as e:
                error = str(e)
            for s in b.get("steps_off") or []:
                if s in wf["steps"]:
                    wf["steps"][s] = {**wf["steps"][s], "enabled": False}
            found = runs.find_samples(b["input"], b.get("pairing")) if b.get("input") else None
            samples = found["samples"] if found else []
            warns = hardware.warnings(wf, tools=tools, n_samples=len(samples) or None)
            if error:
                warns.insert(0, {"level": "error", "step": None, "message": error})
            if found and not samples:
                warns.insert(0, {"level": "error", "step": None, "message":
                                 f"No paired reads found in {b['input']} (pairing on {found['r1']} / {found['r2']})."})
            if found:
                warns += [{"level": "error", "step": None, "message": m} for m in runs.pairing_problems(found)]
            return self._send(200, {"warnings": warns, "samples": samples, "pairing": found})
        if g and p == ["browse"]:
            path = Path(q.get("path") or Path.home()).expanduser()
            dirs = sorted((d.name for d in path.iterdir() if d.is_dir() and not d.name.startswith(".")), key=runs.natural) if path.is_dir() else []
            reads = sorted((f.name for f in path.glob("*.gz")), key=runs.natural) if path.is_dir() else []
            return self._send(200, {"path": str(path.resolve()), "parent": str(path.resolve().parent), "dirs": dirs, "reads": reads[:200]})

        if g and p == ["databases"]:
            return self._send(200, list(catalog.load_databases().values()))
        if post and len(p) == 3 and p[0] == "databases" and p[2] == "install":
            db_id = p[1]
            return self._send(200, _job(f"Install database {db_id}", lambda log, prog: databases.install(db_id, log, prog)))

        if g and p == ["packs"]:
            return self._send(200, toolpacks.list_packs())
        if post and p == ["packs", "install"]:
            src = self._body()["source"]
            return self._send(200, _job(f"Install tool pack {src}", lambda log, prog: toolpacks.install(src, log)))
        if post and p == ["packs", "uninstall"]:
            toolpacks.uninstall(self._body()["id"])
            return self._send(200, {"ok": True})
        if g and len(p) == 2 and p[0] == "jobs":
            return self._send(200, JOBS[p[1]])

        if g and p == ["runs"]:
            return self._send(200, runs.list_runs())
        if post and p == ["runs"]:
            b = self._body()
            run = runs.start(b["workflow"], b.get("version"), b["input"], name=b.get("name"),
                             cleanup=b.get("cleanup", True), steps_off=b.get("steps_off") or [],
                             pairing=b.get("pairing"))
            return self._send(200, run)
        if g and len(p) == 2 and p[0] == "runs":
            return self._send(200, runs.detail(p[1]))
        if post and len(p) == 3 and p[0] == "runs" and p[2] == "stop":
            return self._send(200, {"stopped": runs.stop(p[1])})
        if delete and len(p) == 2 and p[0] == "runs":
            runs.delete(p[1])
            return self._send(200, {"ok": True})
        if g and len(p) >= 3 and p[0] == "runs" and p[2] == "file":
            return self._file(runs.result_file(p[1], q["path"]), download=q.get("download") == "1")
        if post and len(p) == 2 and p[0] == "weblog":
            runs.weblog(p[1], self._body())
            return self._send(200, {"ok": True})
        raise KeyError("/".join(p))


def serve(host="127.0.0.1", port=8642, open_browser=True):
    url = f"http://{'localhost' if host in ('127.0.0.1', '0.0.0.0') else host}:{port}/"
    try:
        httpd = ThreadingHTTPServer((host, port), Handler)
    except OSError:
        # Already running (e.g. the desktop shortcut clicked twice): just open it
        import urllib.request
        try:
            urllib.request.urlopen(url + "api/hardware", timeout=5)
        except OSError:
            raise SystemExit(f"Port {port} is in use by another program. Try: ntg ui --port {port + 1}")
        print(f"NTG pipeline UI is already running: {url}")
        if open_browser:
            _open(url)
        return
    runs.WEBLOG_URL = f"http://127.0.0.1:{port}/api/weblog"
    print(f"NTG pipeline UI: {url}  (Ctrl+C to stop)", flush=True)
    if open_browser:
        threading.Timer(1.0, lambda: _open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


def _restart():
    # Same process (so the desktop shortcut's window stays), new code; the browser is already open.
    args = sys.argv[1:] + ([] if "--no-browser" in sys.argv else ["--no-browser"])
    os.execv(sys.executable, [sys.executable, "-m", "app.cli", *args])


def _open(url):
    import shutil
    import subprocess
    # Under WSL, open the Windows browser. Wait for the launcher to return:
    # if this process exits first (UI already running), WSL kills the launch.
    for cmd in (["wslview", url], ["cmd.exe", "/c", "start", '""', url.replace("&", "^&")], ["xdg-open", url]):
        if shutil.which(cmd[0]):
            try:
                subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd="/", timeout=20)
                return
            except (OSError, subprocess.TimeoutExpired):
                pass
    webbrowser.open(url)
