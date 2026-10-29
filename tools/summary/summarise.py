#!/usr/bin/env python3
"""Builds the Galaxy RDH v5.2 output tables from the tools' own outputs.

Replaces Galaxy's text steps (cut, sed, grep, join, collapse, concat, add
header, transpose). Each table is made the same way the Galaxy workflow makes
it; the step numbers in the comments are the Galaxy step ids in
reference/Galaxy-Workflow-Genomic_Assembly_and_analysis_-_RDH_version_5.2.ga.

Usage: summarise.py --in-dir <dir> --out-dir <dir>
  <in-dir>/in_<name>/ holds every sample's file for input <name>; missing
  folders (tool switched off) just skip that table.
Standard library only.
"""
import argparse
import csv
import re
import sys
from pathlib import Path

DEPTH_RE = re.compile(r"\[shovill\] Estimated sequencing depth:", re.I)


def files(in_dir, name, suffix=""):
    d = in_dir / f"in_{name}"
    return sorted(p for p in d.glob(f"*{suffix}") if p.is_file()) if d.is_dir() else []


def sample_of(path, suffix):
    n = path.name
    return n[: -len(suffix)] if suffix and n.endswith(suffix) else path.stem


def read_rows(path):
    with open(path, newline="", encoding="utf-8", errors="replace") as fh:
        return [row for row in csv.reader(fh, delimiter="\t")]


def write(out_dir, name, rows):
    out = out_dir / name
    with open(out, "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh, delimiter="\t", lineterminator="\n").writerows(rows)
    print(f"wrote {out.name} ({len(rows)} lines)")


# ---------------------------------------------------------------- tables

def mlst(in_dir, out_dir):
    # steps 0, 2, 20, 28: header line + every sample's mlst line
    fs = files(in_dir, "mlst", ".mlst.tsv")
    if not fs:
        return
    rows = [["name", "scheme", "ST"] + [f"allele{i}" for i in range(1, 8)]]
    for f in fs:
        rows += [r for r in read_rows(f) if r]
    write(out_dir, "mlst_output.tsv", rows)


def sistr(in_dir, out_dir):
    # steps 19, 27: 'genome' column first, renamed 'name'; one header for all samples
    fs = files(in_dir, "sistr", ".sistr.tab")
    if not fs:
        return
    header, body = None, []
    for f in fs:
        rows = read_rows(f)
        if not rows:
            continue
        h = rows[0]
        g = h.index("genome") if "genome" in h else 0
        order = [g] + [i for i in range(len(h)) if i != g]
        if header is None:
            header = ["name"] + [h[i] for i in order[1:]]
        body += [[r[i] if i < len(r) else "" for i in order] for r in rows[1:]]
    write(out_dir, "sistr_output.tsv", [header] + body)


def abricate(in_dir, out_dir):
    # steps 23, 30: per sample BAC (ncbi, with header) + CDIP (vfdb, no header),
    # then all samples with a single header line
    bac = {sample_of(f, ".abricate.tsv"): f for f in files(in_dir, "abricate_bac", ".abricate.tsv")}
    cdip = {sample_of(f, ".abricate.tsv"): f for f in files(in_dir, "abricate_cdip", ".abricate.tsv")}
    if not bac and not cdip:
        return
    rows, have_header = [], False
    for s in sorted(set(bac) | set(cdip)):
        part = read_rows(bac[s]) if s in bac else []
        if s in cdip:
            part += [r for r in read_rows(cdip[s]) if r and not r[0].startswith("#FILE")]
        if part and part[0] and part[0][0].startswith("#FILE"):
            if have_header:
                part = part[1:]
            have_header = True
        rows += part
    write(out_dir, "abricate_output.tsv", rows)


def quast(in_dir, out_dir):
    # steps 25, 32, 36: transpose report.tsv, 'Assembly' -> 'name'
    fs = [f for f in files(in_dir, "quast") if f.name == "report.tsv"]
    if not fs:
        return
    rows = read_rows(fs[0])
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    t = [list(col) for col in zip(*rows)]
    if t and t[0] and t[0][0] == "Assembly":
        t[0][0] = "name"
    write(out_dir, "quast_output.tsv", t)


def emmtyper(in_dir, out_dir):
    # steps 22, 29, 34, 38: sample name appended as the last column, all samples
    # concatenated, then Galaxy's Cut c6,c2,c3,c4,c1 (name, hits, clusters,
    # emm type, isolate) for the 5-column short format
    fs = files(in_dir, "emmtyper", ".emmtyper.tsv")
    if not fs:
        return
    rows = []
    for f in fs:
        s = sample_of(f, ".emmtyper.tsv")
        for r in read_rows(f):
            if not r:
                continue
            r = r + [s]
            name = r[-1]
            rows.append([name] + r[1:4] + [r[0]])
    write(out_dir, "emmtyper_output.tsv", rows)


def depth_table(in_dir):
    # steps 15, 24, 31, 35: depth line from each shovill log, number only
    out = {}
    for f in files(in_dir, "shovill_log", ".shovill.log"):
        s = sample_of(f, ".shovill.log")
        for line in open(f, encoding="utf-8", errors="replace"):
            if DEPTH_RE.search(line):
                out[s] = DEPTH_RE.split(line, 1)[1].strip()
                break
    return out


def checkm_depth(in_dir, out_dir):
    # steps 39, 41: CheckM table joined to the depth table on the sample name
    fs = [f for f in files(in_dir, "checkm") if f.name == "checkm_results.tsv"]
    depth = depth_table(in_dir)
    if not fs:
        if depth:
            write(out_dir, "depth_output.tsv",
                  [["name", "[shovill] Estimated sequencing depth:"]] + [[s, d] for s, d in sorted(depth.items())])
        return
    rows = read_rows(fs[0])
    out = [rows[0] + ["[shovill] Estimated sequencing depth:"]]
    for r in rows[1:]:
        if r and r[0] in depth:  # Galaxy join drops rows without a match
            out.append(r + [depth[r[0]]])
    write(out_dir, "checkm_depth_output.tsv", out)


def bracken(in_dir, out_dir):
    # steps 26, 33, 37, 40, 42, 43, 44: per sample, the first 4 species rows of
    # the Bracken table with the sample name in front and a header line;
    # samples concatenated (the header repeats per sample, as in Galaxy)
    fs = files(in_dir, "bracken", ".bracken.tsv")
    if not fs:
        return
    header = ["name", "species_name", "taxonomy_id", "taxonomy_lvl", "kraken_assigned_reads",
              "added_reads", "new_est_reads", "fraction_total_reads"]
    rows = []
    for f in fs:
        s = sample_of(f, ".bracken.tsv")
        data = [r for r in read_rows(f)[1:] if r]
        rows.append(header)
        rows += [[s] + r for r in data[:4]]
    write(out_dir, "bracken_output.tsv", rows)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--in-dir", type=Path, default=Path("."))
    ap.add_argument("--out-dir", type=Path, default=Path("out"))
    a = ap.parse_args(argv)
    a.out_dir.mkdir(parents=True, exist_ok=True)
    for table in (mlst, sistr, abricate, quast, emmtyper, checkm_depth, bracken):
        table(a.in_dir, a.out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
