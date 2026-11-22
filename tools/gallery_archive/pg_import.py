#!/usr/bin/env python3
"""Import archived JPG stills and DRX sidecars into shared gallery albums.
With no arguments, import all archive jobs except those in skip_import_jobs.
Save a local project first. Progress is recorded in gallery_tmp for resuming."""
import sys, os, time, shutil, math
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "prep"))
from hprep_config import cfg, setting_path
sys.path.append(str(setting_path("resolve_modules")))
import DaVinciResolveScript as dvr

SHARE = setting_path("gallery_share")
SKIP = cfg("skip_import_jobs")
if not isinstance(SKIP, list) or not all(isinstance(name, str) for name in SKIP):
    raise SystemExit("Config key 'skip_import_jobs' must be a list of job names")
MAXPER = 1500
BATCH = 100
MINFREE = 35 * 1024 ** 3
TMP = setting_path("gallery_tmp")
TMP.mkdir(parents=True, exist_ok=True)
DONE = TMP / "pg_import_done.txt"
LOG = TMP / "pg_import_log.txt"

def log(*a):
    s = time.strftime("%H:%M:%S ") + " ".join(str(x) for x in a)
    print(s, flush=True); open(LOG, "a").write(s + "\n")

def wait_for_space():
    waited = 0
    while shutil.disk_usage(TMP).free < MINFREE:
        if waited == 0: log("  low disk: waiting for disk space",
                            round(shutil.disk_usage(TMP).free / 1024 ** 3, 1), "GB free")
        time.sleep(60); waited += 60
        if waited > 4 * 3600: log("  gave up waiting 4 h"); sys.exit(2)

def job_files(job):
    jd = os.path.join(SHARE, job); out = []
    for alb in sorted(d for d in os.listdir(jd) if os.path.isdir(os.path.join(jd, d))):
        for f in sorted(os.listdir(os.path.join(jd, alb))):
            if f.endswith(".jpg"): out.append(os.path.join(jd, alb, f))
    return out

done = set(l.rstrip("\n") for l in open(DONE)) if os.path.exists(DONE) else set()
r = dvr.scriptapp("Resolve"); pm = r.GetProjectManager(); p = pm.GetCurrentProject(); g = p.GetGallery()
log("project", p.GetName())
if not g.GetGalleryPowerGradeAlbums():
    log("no PowerGrade albums visible: load a saved project of the Local Database first"); sys.exit(1)

jobs = sys.argv[1:] or sorted(
    (d for d in os.listdir(SHARE) if os.path.isdir(os.path.join(SHARE, d)) and not d.startswith("_") and d not in SKIP),
    key=lambda j: len(job_files(j)))

for job in jobs:
    files = job_files(job)
    if not files: log("skip (no stills)", job); continue
    parts = max(1, math.ceil(len(files) / MAXPER)); size = math.ceil(len(files) / parts)
    log("JOB", job, len(files), "stills ->", parts, "album(s)")
    for i in range(parts):
        chunk = files[i * size:(i + 1) * size]
        name = job if parts == 1 else f"{job} {i + 1:02d}"
        existing = {g.GetAlbumName(a): a for a in g.GetGalleryPowerGradeAlbums()}
        alb = existing.get(name)
        if alb is None:
            alb = g.CreateGalleryPowerGradeAlbum(); g.SetAlbumName(alb, name)
        todo = [f for f in chunk if f"{name}\t{os.path.relpath(f, SHARE)}" not in done]
        for b in range(0, len(todo), BATCH):
            wait_for_space()
            batch = todo[b:b + BATCH]; before = len(alb.GetStills())
            ok = alb.ImportStills(batch); got = len(alb.GetStills()) - before
            if got != len(batch): log(f"  {name}: batch of {len(batch)} gave {got} (ok={ok})")
            if got > 0:
                with open(DONE, "a") as fh:
                    for f in batch[:got]: fh.write(f"{name}\t{os.path.relpath(f, SHARE)}\n")
        log(f"  {name}: {len(alb.GetStills())} stills in album")
log("ALL DONE")
