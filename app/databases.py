"""Downloads and unpacks reference databases listed in databases/catalog.yml."""
import os
import shutil
import tarfile
import urllib.request
from pathlib import Path

from . import catalog


def install(db_id, log=print, progress=None):
    db = catalog.load_databases()[db_id]
    dest = Path(db["path"])
    if db["installed"]:
        log(f"{db_id}: already installed at {dest}")
        return dest
    tmp = dest.parent / (dest.name + ".download")
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"{db_id}: downloading {db['url']} ({db['size_gb']} GB)")
    with urllib.request.urlopen(db["url"]) as r, open(tmp, "wb") as fh:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while chunk := r.read(1 << 22):
            fh.write(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    log(f"{db_id}: unpacking")
    work = dest.parent / (dest.name + ".partial")
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir()
    with tarfile.open(tmp) as tar:
        tar.extractall(work, filter="data")
    os.replace(work, dest)
    tmp.unlink()
    log(f"{db_id}: installed at {dest}")
    return dest
