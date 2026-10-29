"""ntg command line: compile workflows, run them, manage databases and tool packs, start the UI."""
import argparse
import sys
from pathlib import Path

from . import catalog, compiler


def cmd_compile(a):
    wf = catalog.load_workflow(a.workflow, a.version)
    if a.root:
        # The committed copy at the repo root, so `nextflow run .` works without the UI
        main_nf, params_config = compiler.compile_workflow(wf)
        (catalog.ROOT / "main.nf").write_text(main_nf, encoding="utf-8", newline="\n")
        (catalog.ROOT / "conf" / "workflow.params.config").write_text(params_config, encoding="utf-8", newline="\n")
        print(f"compiled {wf['id']} v{wf['_version']} -> main.nf, conf/workflow.params.config")
    else:
        dest = compiler.write_bundle(wf, a.out)
        print(f"compiled {wf['id']} v{wf['_version']} -> {dest}")


def cmd_run(a):
    from . import runs
    run = runs.start(a.workflow, a.version, a.input, extra=a.nextflow_args, wait=True)
    sys.exit(0 if run["status"] == "succeeded" else 1)


def cmd_ui(a):
    from . import server
    server.serve(a.host, a.port, open_browser=not a.no_browser)


def cmd_db(a):
    from . import databases
    if a.action == "list":
        for db in catalog.load_databases().values():
            print(f"{'installed' if db['installed'] else 'missing  '}  {db['id']:<22} {db['name']} ({db['size_gb']} GB)")
    else:
        for db_id in a.ids:
            databases.install(db_id, log=print)


def cmd_tools(a):
    from . import toolpacks
    if a.action == "list":
        for t in catalog.load_tools().values():
            print(f"{t['id']:<22} {t.get('version', ''):<10} {t['_source']:<20} {t['name']}")
    elif a.action == "packs":
        for p in toolpacks.list_packs():
            print(f"{'installed' if p['installed'] else 'available'}  {p['id']:<30} {p['repo']}")
    elif a.action == "install":
        toolpacks.install(a.source, log=print)


def cmd_hardware(a):
    from . import hardware
    import json
    print(json.dumps(hardware.check(), indent=2))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ntg", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("ui", help="start the browser UI")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8642)
    p.add_argument("--no-browser", action="store_true")
    p.set_defaults(fn=cmd_ui)

    p = sub.add_parser("compile", help="compile a workflow into Nextflow")
    p.add_argument("workflow")
    p.add_argument("--version", type=int)
    p.add_argument("--out", type=Path, default=Path("build"))
    p.add_argument("--root", action="store_true", help="write main.nf at the repo root")
    p.set_defaults(fn=cmd_compile)

    p = sub.add_parser("run", help="run a workflow on a reads folder")
    p.add_argument("workflow")
    p.add_argument("input", help="folder of paired reads")
    p.add_argument("--version", type=int)
    p.add_argument("nextflow_args", nargs=argparse.REMAINDER)
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("db", help="reference databases")
    p.add_argument("action", choices=["list", "install"])
    p.add_argument("ids", nargs="*")
    p.set_defaults(fn=cmd_db)

    p = sub.add_parser("tools", help="tools and tool packs")
    p.add_argument("action", choices=["list", "packs", "install"])
    p.add_argument("source", nargs="?", help="pack id or git URL")
    p.set_defaults(fn=cmd_tools)

    p = sub.add_parser("hardware", help="show this computer's specs and warnings")
    p.set_defaults(fn=cmd_hardware)

    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
