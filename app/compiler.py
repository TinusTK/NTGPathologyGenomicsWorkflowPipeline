"""Compiles a workflow (workflow.yml + tool.yml files) into a Nextflow pipeline.

Output: main.nf (one process per step, wiring, on/off logic) and params.config
(step switches, settings, database paths). Runs never edit these by hand; the
UI compiles a fresh copy into every run folder so each run keeps exactly the
code that made it.

Tool command contract (see docs/TOOLS.md):
  SAMPLE, CPUS, MEM_GB          sample id ('all' for all-sample steps), resources
  S_<setting>                   every setting, as text ('true'/'false' for booleans)
  IN_<input>                    per-sample input file (reads: IN_<input>_1 / _2)
  IN_<input>_DIR                all-sample input: folder holding every sample's file
  DB_<database>                 database folder
  TOOL_DIR                      the tool's own folder (bundled scripts)
  write results under out/; notes for provenance go in .ntg_notes ("key: value" lines)
"""
from pathlib import Path

from . import catalog

INPUT = "input"


class CompileError(Exception):
    pass


def _gs(text):
    """Escape text for a Groovy triple-quoted GString."""
    return text.replace("\\", "\\\\").replace("$", "\\$").replace('"""', '\\"\\"\\"')


def _q(value):
    """Groovy/config literal."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


def resolve(wf, tools):
    """Check the workflow and return ordered steps with their tools and resolved settings."""
    steps = wf.get("steps") or {}
    order, seen = [], set()

    def visit(sid, stack=()):
        if sid in seen:
            return
        if sid in stack:
            raise CompileError(f"steps form a loop: {' -> '.join(stack + (sid,))}")
        step = steps[sid]
        for sources in (step.get("inputs") or {}).values():
            for src in sources:
                if src != INPUT:
                    up = src.split(".")[0]
                    if up not in steps:
                        raise CompileError(f"step {sid}: input from unknown step '{up}'")
                    visit(up, stack + (sid,))
        seen.add(sid)
        order.append(sid)

    for sid in steps:
        if not catalog.ID_RE.match(sid):
            raise CompileError(f"step id '{sid}': lowercase letters, digits and _ only")
        visit(sid)

    resolved = []
    for sid in order:
        step = steps[sid]
        tool = tools.get(step["tool"])
        if tool is None:
            raise CompileError(f"step {sid}: tool '{step['tool']}' is not installed")
        settings = {}
        for name, spec in (tool.get("settings") or {}).items():
            settings[name] = (step.get("settings") or {}).get(name, spec.get("default", ""))
        for name in (step.get("settings") or {}):
            if name not in settings:
                raise CompileError(f"step {sid}: tool {tool['id']} has no setting '{name}'")
        for name, spec in (tool.get("inputs") or {}).items():
            srcs = (step.get("inputs") or {}).get(name) or []
            if not srcs and not spec.get("optional"):
                raise CompileError(f"step {sid}: input '{name}' is not connected")
            for src in srcs:
                if src == INPUT:
                    continue
                up, _, out = src.partition(".")
                up_tool = tools[steps[up]["tool"]]
                if out not in (up_tool.get("outputs") or {}):
                    raise CompileError(f"step {sid}: step {up} has no output '{out}'")
        resolved.append({"id": sid, "step": step, "tool": tool, "settings": settings})
    return resolved


def runnable(wf, tools):
    """{step: will it run} with the same rules as the generated code: a step runs
    when it is enabled and every required input has at least one source that runs."""
    ok = {}
    for r in resolve(wf, tools):
        sid, step, tool = r["id"], r["step"], r["tool"]
        good = step.get("enabled", True)
        for name, spec in (tool.get("inputs") or {}).items():
            srcs = (step.get("inputs") or {}).get(name) or []
            if not spec.get("optional") and not any(s == INPUT or ok.get(s.split(".")[0]) for s in srcs):
                good = False
        ok[sid] = bool(good)
    return ok


# ---------------------------------------------------------------- code generation

def _process(r):
    sid, tool, step = r["id"], r["tool"], r["step"]
    per_sample = tool.get("scope", "per_sample") == "per_sample"
    res = {**(tool.get("resources") or {}), **(step.get("resources") or {})}
    name = f"STEP_{sid}"
    inputs = tool.get("inputs") or {}
    dbs = tool.get("databases") or []
    files = tool.get("files") or []

    L = [f"// {step.get('label') or tool['name']}: {tool['name']} {tool.get('version', '')}".rstrip(),
         f"process {name} {{"]
    L.append(f"    tag {{ \"${{meta.id}}\" }}" if per_sample else f"    tag '{sid}'")
    L.append(f"    container '{tool['container']}'")
    L.append(f"    cpus {int(res.get('cpus', 1))}")
    if res.get("memory_setting"):
        # Memory from a setting: a number = that many GB; 0/blank = Auto = the tool's
        # amount (or its low-memory amount), but leave 2 GB for the rest of the computer.
        ram = f"params.settings.{sid}.{res['memory_setting']}.toString()"
        want = str(int(res.get("memory_gb", 2)))
        if res.get("low_memory_setting"):
            want = (f"(params.settings.{sid}.{res['low_memory_setting']}.toString() == 'true' ? "
                    f"{int(res['low_memory_gb'])} : {want})")
        L += ["    memory {",
              f"        def ram = {ram}",
              "        def total = (java.lang.management.ManagementFactory.operatingSystemMXBean.totalMemorySize / 1073741824) as int",
              f"        \"${{ram.isInteger() && ram.toInteger() > 0 ? ram.toInteger() : Math.min({want}, Math.max(4, total - 2))}} GB\"",
              "    }"]
    else:
        L.append(f"    memory '{int(res.get('memory_gb', 2))} GB'")
    pub = step.get("publish") or (f"samples/${{meta.id}}/{sid}" if per_sample else f"combined/{sid}")
    L.append(f"    publishDir path: {{ \"${{params.outdir}}/{pub}\" }}, mode: 'copy', "
             "saveAs: { fn -> fn.startsWith('out/') ? fn.substring(4) : null }")
    L.append("")
    L.append("    input:")
    if per_sample:
        parts = ["val(meta)"] + [f"path(in_{n})" for n in inputs]
        L.append("    tuple " + ", ".join(parts))
    else:
        L += [f"    path(in_{n}, stageAs: 'in_{n}/*')" for n in inputs]
    L += [f"    path(db_{d})" for d in dbs]
    L += [f"    path(toolfile_{i})" for i, _ in enumerate(files)]
    L.append("")
    L.append("    output:")
    for oname, spec in (tool.get("outputs") or {}).items():
        pattern = spec["path"].replace("{sample}", "${meta.id}")
        pattern = f'"{pattern}"' if "${" in pattern else f"'{pattern}'"
        opt = ", optional: true" if spec.get("optional") else ""
        if per_sample:
            L.append(f"    tuple val(meta), path({pattern}){opt}, emit: {oname}")
        else:
            L.append(f"    path({pattern}){opt}, emit: {oname}")
    L.append("    path('versions.yml'), emit: versions")
    L.append("")
    L.append("    script:")
    if not per_sample:
        L.append("    def meta = [id: 'all']")
    env = [f"export SAMPLE='${{meta.id}}' CPUS=${{task.cpus}} MEM_GB=${{task.memory.toGiga()}}",
           f"export TOOL_DIR=\"\\$PWD/tooldir\" STEP='{sid}'",
           "mkdir -p out tooldir"]
    for i, f in enumerate(files):
        env.append(f"cp -rL \"${{toolfile_{i}}}\" tooldir/{f}")
    for s in r["settings"]:
        env.append(f"export S_{s}='${{params.settings.{sid}.{s}}}'")
    for n, spec in inputs.items():
        if not per_sample:
            env.append(f"mkdir -p in_{n}; export IN_{n}_DIR=\"\\$PWD/in_{n}\"")
        elif spec.get("type") == "reads":
            env.append(f"export IN_{n}_1='${{in_{n}[0]}}' IN_{n}_2='${{in_{n}[1]}}'")
        else:
            env.append(f"export IN_{n}='${{in_{n}}}'")
    for d in dbs:
        env.append(f"export DB_{d}=\"\\$(readlink -f '${{db_{d}}}')\"")
    versions = tool.get("versions") or {tool["id"]: tool.get("version_command", "echo unknown")}
    vlines = [f"printf '%s: %s\\n' '{k}' \"$( ({cmd}) 2>&1 | head -1 )\" >> versions.yml"
              for k, cmd in versions.items()]
    vlines.append(f"echo 'container.{name}: {tool['container']}' >> versions.yml")
    vlines.append(f"[ -f .ntg_notes ] && sed 's/^/note.{sid}./' .ntg_notes >> versions.yml || true")

    body = "\n".join(env) + "\n\n" + _gs(tool["command"].rstrip()) + "\n\n" + _gs("\n".join(vlines))
    L.append('    """')
    L += [("    " + line) if line else "" for line in body.split("\n")]
    L.append('    """')
    L.append("}")
    return "\n".join(L)


def _source_expr(src, run_of):
    if src == INPUT:
        return "ch_input", "true"
    up, _, out = src.partition(".")
    return f"ch_{up}_{out}", f"run_{up}"


def _pick(srcs, run_of):
    """First available source: (channel expression, availability expression)."""
    exprs = [_source_expr(s, run_of) for s in srcs]
    chan, avail = exprs[-1]
    for c, a in reversed(exprs[:-1]):
        chan = f"({a} ? {c} : {chan})"
    avail_all = " || ".join(a for _, a in exprs) if exprs else "false"
    return chan, f"({avail_all})"


def _wiring(r):
    sid, tool, step = r["id"], r["tool"], r["step"]
    per_sample = tool.get("scope", "per_sample") == "per_sample"
    inputs = tool.get("inputs") or {}
    name = f"STEP_{sid}"
    L = [f"    // ---- {sid}"]
    for oname in (tool.get("outputs") or {}):
        L.append(f"    def ch_{sid}_{oname} = channel.empty()")
    needs, args = [], []
    picks = {}
    for n, spec in inputs.items():
        srcs = (step.get("inputs") or {}).get(n) or []
        if srcs:
            picks[n] = _pick(srcs, None)
            if not spec.get("optional"):
                needs.append(picks[n][1])
    # toString(): "--steps.x false" on the command line arrives as the text 'false'
    cond = " && ".join([f"params.steps.{sid}.toString() == 'true'"] + needs)
    L.append(f"    def run_{sid} = {cond}")
    L.append(f"    if (run_{sid}) {{")
    if per_sample:
        chans = [picks[n][0] for n in inputs if n in picks]
        expr = chans[0]
        for c in chans[1:]:
            expr = f"{expr}.join({c})"
        args.append(expr)
    else:
        for n in inputs:
            if n in picks:
                c, a = picks[n]
                args.append(f"({a} ? {c}.map {{ it[1] }}.collect().ifEmpty([]) : channel.value([]))")
            else:
                args.append("channel.value([])")
    for d in tool.get("databases") or []:
        args.append(f"file(params.databases.{d}, checkIfExists: true)")
    for f in tool.get("files") or []:
        args.append(f"file(\"${{projectDir}}/tools/{tool['id']}/{f}\", checkIfExists: true)")
    L.append(f"        {name}({', '.join(args)})")
    for oname in (tool.get("outputs") or {}):
        if per_sample:
            L.append(f"        ch_{sid}_{oname} = {name}.out.{oname}")
        else:
            L.append(f"        ch_{sid}_{oname} = {name}.out.{oname}.map {{ f -> tuple([id: 'all'], f) }}")
    L.append(f"        versions = versions.mix({name}.out.versions)")
    L.append("    }")
    return "\n".join(L)


def compile_workflow(wf, tools=None, databases=None):
    tools = tools if tools is not None else catalog.load_tools()
    databases = databases if databases is not None else catalog.load_databases()
    steps = resolve(wf, tools)
    for r in steps:
        for d in r["tool"].get("databases") or []:
            if d not in databases:
                raise CompileError(f"step {r['id']}: database '{d}' is not in databases/catalog.yml")

    head = [
        "#!/usr/bin/env nextflow",
        f"// GENERATED by `ntg compile` from workflow '{wf['id']}' v{wf.get('version', 1)}: do not edit by hand.",
        f"// {wf['name']}",
        "",
    ]
    procs = [_process(r) for r in steps]
    pattern = (wf.get("input") or {}).get("pattern", "*_R{1,2}*.gz")
    body = [
        "workflow {",
        "    main:",
        "    // The UI pairs the reads and passes them as samples.csv (sample,r1,r2);",
        "    // without one, pair the files in --input with reads_pattern.",
        "    def ch_input = params.samplesheet",
        "        ? channel.fromPath(params.samplesheet, checkIfExists: true).splitCsv(header: true)",
        "            .map { row -> tuple([id: row.sample], [file(row.r1, checkIfExists: true), file(row.r2, checkIfExists: true)]) }",
        f"        : channel.fromFilePairs(\"${{params.input}}/${{params.reads_pattern}}\", checkIfExists: true)",
        "            .map { id, files -> tuple([id: id], files) }",
        "    def versions = channel.empty()",
        "",
    ]
    body += [_wiring(r) + "\n" for r in steps]
    body += [
        "    versions",
        "        .map { it.text.trim() }",
        "        .unique()",
        "        .collectFile(name: 'tool_versions.yml', storeDir: \"${params.outdir}/pipeline_info\", sort: true, newLine: true)",
        "",
        "    onComplete:",
        "    Provenance.write(workflow, params, projectDir)",
        "    log.info \"Provenance: ${params.outdir}/pipeline_info/provenance.json\"",
        "}",
    ]
    main_nf = "\n".join(head + procs + [""] + body) + "\n"

    P = ["// GENERATED by `ntg compile`: step switches, settings and databases for this workflow.",
         "params {",
         f"    workflow_id      = {_q(wf['id'])}",
         f"    workflow_version = {int(wf.get('version', 1))}",
         f"    workflow_name    = {_q(wf['name'])}",
         f"    reads_pattern    = {_q(pattern)}",
         "",
         "    steps {"]
    P += [f"        {r['id']} = {_q(bool(r['step'].get('enabled', True)))}" for r in steps]
    P += ["    }", "", "    settings {"]
    for r in steps:
        if r["settings"]:
            P.append(f"        {r['id']} {{")
            P += [f"            {k} = {_q(v)}" for k, v in r["settings"].items()]
            P.append("        }")
    P += ["    }", "", "    databases {"]
    used = sorted({d for r in steps for d in (r["tool"].get("databases") or [])})
    P += [f"        {d} = \"${{params.db_dir}}/{databases[d]['install_dir']}\"" for d in used]
    P += ["    }", "}"]
    params_config = "\n".join(P) + "\n"
    return main_nf, params_config


def write_bundle(wf, dest, tools=None, databases=None):
    """Compile into folder dest: main.nf, params.config, nextflow.config, lib/, tools/ used."""
    import shutil
    tools = tools if tools is not None else catalog.load_tools()
    main_nf, params_config = compile_workflow(wf, tools, databases)
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "main.nf").write_text(main_nf, encoding="utf-8", newline="\n")
    (dest / "params.config").write_text(params_config, encoding="utf-8", newline="\n")
    (dest / "nextflow.config").write_text(
        f"includeConfig '{catalog.ROOT.as_posix()}/conf/base.config'\nincludeConfig 'params.config'\n",
        encoding="utf-8", newline="\n")
    shutil.copytree(catalog.ROOT / "lib", dest / "lib", dirs_exist_ok=True)
    for tid in {s["tool"] for s in wf["steps"].values()}:
        shutil.copytree(tools[tid]["_dir"], dest / "tools" / tid, dirs_exist_ok=True)
    catalog.dump_yaml({k: v for k, v in wf.items() if not k.startswith("_")}, dest / "workflow.yml")
    return dest
