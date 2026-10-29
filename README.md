# NTGPathologyGenomicsWorkflowPipeline

A stand-alone version of the Galaxy Australia workflow **"Genomic Assembly and
analysis - RDH version 5.2"**, which types bacterial isolates from paired
Illumina reads. It runs without Galaxy on any Linux machine (or Windows with
WSL2), using [Nextflow](https://www.nextflow.io) and containers pinned to the
same tool versions Galaxy used. A browser UI shows the whole pipeline and the
exact version of every tool and database, and every run records what produced
its results.

The original Galaxy workflow is in [`reference/`](reference/).

> **Status: early development (v0.2.0).**

## What it does

| Step | Tool | Version |
|---|---|---|
| Read preprocessing | fastp | 0.24.0 |
| Genome assembly | Shovill (SPAdes) | 1.4.2 |
| Species identification | Kraken2 + Bracken | 2.1.3 / 3.0 |
| Serovar | SISTR | 1.1.1 |
| MLST | mlst | 2.22.0 |
| AMR and plasmids | staramr | 0.11.0 |
| emm typing | emmtyper | 0.2.0 |
| Resistance / virulence genes | ABRicate (NCBI, VFDB) | 1.4.0 |
| AMR (Australian reporting) | abriTAMR | 1.3.0 |
| Completeness / contamination | CheckM | 1.2.3 |
| Assembly QC | QUAST | 5.3.0 |
| Summary tables | built-in | Galaxy's output tables, made the same way |

Settings match the Galaxy workflow; see
[`workflows/rdh_v5_2/workflow.yml`](workflows/rdh_v5_2/workflow.yml). Where
this pipeline has to differ from Galaxy, the UI shows it next to the setting:

- **Kraken2 database:** Standard-8 (2026-06) instead of Galaxy's full
  Standard (2020) with 2021 Bracken files, so species abundances can differ a little.
- **CheckM tree:** "Auto" uses the reduced tree on computers with less than
  40 GB RAM (Galaxy used the full tree).

Extra tools come as **tool packs** from other git repositories (see below), for
example Shigella typing and *C. diphtheriae* tox/MLST typing; the built-in
workflow "RDH v5.2 + Shigella + C. diphtheriae typing" adds them.

## Requirements

- Linux, or Windows 10/11 with WSL2 (Ubuntu)
- Docker Engine (or Apptainer), Java 17+, Nextflow 25.04+, Python 3.10+ with PyYAML
- About 10 GB of disk for databases, plus room for runs

## Using it

Start the UI (it opens in your browser):

```bash
./ntg ui
```

On Windows, `windows/create-desktop-shortcut.ps1` puts a shortcut on the
desktop that does the same.

In the UI:

- **Workflows**: the pipeline as a graph. Click a step to see its tool,
  version, container, Galaxy step number and every setting with a plain
  description and Galaxy's value. Workflows open **read-only**; **Edit** makes a
  draft, and **Save** always writes a new version (v1, v2, …), so nothing is
  ever overwritten. Built-in workflows can only be saved as a new workflow.
- **Run**: pick a workflow and a reads folder, switch steps off for this run
  (anything that needs a switched-off step is skipped too), choose whether to
  keep intermediate files, and see warnings for this computer (memory, disk,
  missing databases, Docker) before starting.
- **Runs & results**: live progress, summary tables, every result file,
  the Nextflow log and the run's provenance.
- **Tools**: installed tools and tool packs; install a pack from a git URL.
- **Databases**: download the reference databases.
- **This computer**: specs and what they mean for the workflow.

From the command line:

```bash
./ntg run rdh_v5_2 /path/to/reads     # run a workflow
./ntg db list                         # databases
./ntg db install kraken2_standard_8
./ntg tools packs                     # available tool packs
./ntg tools install shigella_typing
./ntg hardware                        # this computer's specs
```

Or with Nextflow directly (the RDH v5.2 workflow is compiled into `main.nf`):

```bash
nextflow run . -profile docker --input /path/to/reads --outdir results
nextflow run . -profile docker --input reads --steps.kraken2 false   # skip a step
```

Reads: a folder of `<sample>_R1….gz` / `<sample>_R2….gz` files; the sample
name is everything before `_R1` / `_R2`.

## Results

```
results/
  samples/<sample>/<step>/   each tool's per-sample output
  combined/<step>/           tools that run on all samples at once (staramr, CheckM, QUAST)
  summary/                   Galaxy's output tables (mlst_output.tsv, quast_output.tsv, ...)
  pipeline_info/
    provenance.json          pipeline commit, workflow + version, tool and database
                             versions, container images + digests, automatic
                             decisions (e.g. CheckM tree), every setting
    report_*.html, timeline_*.html, trace_*.tsv, dag_*.html
```

**Cleanup** (`--cleanup true`, on by default for now) deletes Nextflow's work
folder after a successful run to save space; final results are kept. Turn it
off to keep every intermediate file.

## Adding tools

Each tool is a folder with a `tool.yml` (container, inputs, outputs, settings
with descriptions, command). A **tool pack** points at another git repository,
pins a commit, and says how to get its container; see
[`docs/TOOLS.md`](docs/TOOLS.md) and the examples in [`tools/`](tools/) and
[`packs/`](packs/).

## Citing

If you use this pipeline, please cite this repository (see
[`CITATION.cff`](CITATION.cff)) and the tools it runs (each tool's citation is
in its `tool.yml` and shown in the UI).

## License

MIT, see [`LICENSE`](LICENSE). The tools the pipeline runs keep their own
licenses; the pipeline only pulls their published containers.
