# Writing tools and tool packs

## tool.yml

A tool is a folder `tools/<id>/` with a `tool.yml` and any files it needs.

```yaml
id: mytool                 # lowercase letters, digits, _
name: My tool
version: 1.2.3
category: Typing           # groups tools in the UI
description: What it does, in a sentence or two.
homepage: https://...
license: MIT
citation: "..."
container: quay.io/biocontainers/mytool:1.2.3--h1234_0   # pin an exact tag
scope: per_sample          # or all_samples (runs once on every sample's file)
resources: {cpus: 4, memory_gb: 4}
requirements:              # used for the hardware warnings
  memory_gb: 2             # error below this
  recommended_memory_gb: 8 # warning below this
  disk_gb_per_sample: 1
  note: Shown next to the step.
databases: [kraken2_standard_8]   # ids from databases/catalog.yml
files: [script.py]         # files/folders in the tool folder the command uses

inputs:
  contigs: {type: fasta, description: Assembled contigs.}
  # types: reads (a pair), fasta, tsv, txt, log, any ...; optional: true allowed

outputs:
  report: {type: tsv, path: "out/{sample}.mytool.tsv", description: ...}
  # files under out/ are published to the results; others (e.g. trimmed/) are
  # only passed to the next steps. {sample} is the sample name.

settings:
  minid:
    label: Minimum identity (%)
    type: number            # integer, number, string, boolean, select (+ options: [...])
    default: 90
    description: Shown under the setting in the UI.
  scheme:
    label: Scheme
    type: string
    default: ""
    auto: {value: "", label: Auto-detect}   # gives the setting an Auto on/off toggle

version_command: mytool --version     # or versions: {name: command, ...}

command: |
  mytool --threads "$CPUS" --minid "$S_minid" "$IN_contigs" > "out/$SAMPLE.mytool.tsv"
```

### What the command can use

| Variable | Meaning |
|---|---|
| `SAMPLE` | sample name (`all` for all-sample steps) |
| `CPUS`, `MEM_GB` | resources given to the step |
| `S_<setting>` | each setting, as text (`true` / `false` for booleans) |
| `IN_<input>` | per-sample input file; reads: `IN_<input>_1`, `IN_<input>_2` |
| `IN_<input>_DIR` | all-sample input: folder holding every sample's file |
| `DB_<database>` | database folder |
| `TOOL_DIR` | folder holding the tool's `files` |

Write results under `out/`. Lines `key: value` written to `.ntg_notes` go into
the run's provenance (e.g. a choice made automatically).

## Workflows

`workflows/<id>/workflow.yml` lists steps; each step names a tool, connects its
inputs to `input` (the raw reads) or `<step>.<output>`, and sets settings. A
list of sources means "the first one that runs", e.g.
`reads: [fastp.reads, input]` uses raw reads when fastp is off.
`extends: <id>` starts from another workflow's steps.

Built-in workflows are read-only. Workflows saved from the UI go to
`~/.ntg/workflows/<id>/v<N>.yml`, one file per version.

## Tool packs

A tool pack brings tools from another git repository. It is a file in
`packs/` (or `ntg/pack.yml` inside the tool's own repository, for install from
a git URL):

```yaml
id: my_pack
name: My pack
repo: https://github.com/owner/repo
commit: <full commit hash>      # what gets installed; update to upgrade
description: ...
container:
  image: quay.io/...            # a published image, or:
  build:
    environment_file: environment.yml     # conda env in the repo, or
    # channels: [conda-forge, bioconda]
    # dependencies: [blast=2.16.0, python=3.12]
tools:
  - id: ...                     # same fields as tool.yml
    files: [tool/script.py, database]     # paths inside the repo
```

Installing clones the repo at the pinned commit, builds the container from
the conda environment (micromamba) when needed, and copies each tool's files
to `~/.ntg/tools/<pack>/<tool>/`. The pack's repo and commit are recorded with
each tool and shown in the UI.
