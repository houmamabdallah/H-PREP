#!/usr/bin/env python3
"""Export albums as JPG and DRX into one archive job.
Usage: pg_export.py JOB PROJECT_A ... or pg_export.py --configured"""
import sys, os, time, hashlib, subprocess, shutil, re
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "prep"))
from hprep_config import cfg, setting_path

def configured_jobs():
    projects = cfg("projects")
    if not isinstance(projects, list):
        raise SystemExit("Config key 'projects' must be a list")
    jobs = []
    for entry in projects:
        if isinstance(entry, str) and entry.strip():
            job, names = entry, [entry]
        elif isinstance(entry, dict):
            job, names = entry.get("job"), entry.get("names")
        else:
            raise SystemExit("Expected a project name or a job with names in 'projects'")
        if not isinstance(job, str) or not job.strip() or not isinstance(names, list) or not names or not all(
            isinstance(name, str) and name.strip() for name in names
        ):
            raise SystemExit("Each job in 'projects' needs a job name and a nonempty names list")
        jobs.append((job, names))
    return jobs


def safe(s):
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", s).strip("_")
    if name in ("", ".", ".."):
        raise SystemExit("Expected a usable project or album folder name")
    return name

def export_job(pm, job, projects, dest, scratch, ffmpeg, log):
    jobdir = os.path.join(dest, job); os.makedirs(jobdir, exist_ok=True)
    seen = set()
    # seed dedupe set from an existing manifest (re-runs)
    mp = os.path.join(jobdir, "_manifest.tsv")
    if os.path.exists(mp):
        with open(mp) as previous:
            for line in previous:
                parts = line.rstrip("\n").split("\t")
                if len(parts) >= 5:
                    seen.add(parts[4])

    with open(mp, "a") as man:
        for pname in projects:
            p = None
            for attempt in range(4):
                p = pm.LoadProject(pname)
                if p: break
                log(" retry load", pname, attempt+1); time.sleep(15)
            if not p: log("FAIL load", pname); continue
            time.sleep(6)  # network-DB projects refresh the gallery ~1 s after load; stale handles export nothing
            g = p.GetGallery(); albums = g.GetGalleryStillAlbums()
            log("PROJECT", pname, "albums", len(albums))
            for ai in range(len(albums)):
                alb = albums[ai]
                aname = g.GetAlbumName(alb); stills = alb.GetStills()
                if not stills: continue
                tmp = tempfile.mkdtemp(prefix="album-", dir=scratch)
                prefix = safe(pname) + "__" + safe(aname) + "_"
                ok = alb.ExportStills(stills, tmp, prefix, "jpg")
                files = sorted(f for f in os.listdir(tmp) if f.endswith(".jpg"))
                if not ok and len(files) == 0 and stills:
                    # stale handle: re-fetch the gallery and retry once
                    time.sleep(3); g = p.GetGallery(); albums = g.GetGalleryStillAlbums(); alb = albums[ai]; stills = alb.GetStills()
                    ok = alb.ExportStills(stills, tmp, prefix, "jpg")
                    files = sorted(f for f in os.listdir(tmp) if f.endswith(".jpg"))
                    log("  refetched album, retry ok", ok, "files", len(files))
                if len(files) < len(stills) and len(files) == 0:
                    # Resolve's JPG writer fails on some stills: export DPX and convert with ffmpeg
                    ok2 = alb.ExportStills(stills, tmp, prefix, "dpx")
                    for d in sorted(f for f in os.listdir(tmp) if f.endswith(".dpx")):
                        j = os.path.join(tmp, d[:-4] + ".jpg")
                        if not os.path.exists(j):
                            subprocess.run([ffmpeg, "-v", "error", "-y", "-i", os.path.join(tmp, d), "-q:v", "2", j])
                        log("Removing temporary file:", os.path.join(tmp, d))
                        os.remove(os.path.join(tmp, d))
                    files = sorted(f for f in os.listdir(tmp) if f.endswith(".jpg"))
                    log("  dpx fallback ok", ok2, "jpg now", len(files))
                log(" album", aname, "stills", len(stills), "exported", len(files), "ok", ok)
                outdir = os.path.join(jobdir, safe(aname)); os.makedirs(outdir, exist_ok=True)
                new = dup = 0
                for f in files:
                    src = os.path.join(tmp, f); drx = src[:-4] + ".drx"
                    h = hashlib.md5(open(src, "rb").read()).hexdigest()
                    if h in seen: dup += 1; continue
                    seen.add(h)
                    # downscale to 1920 wide, q2 (~0.5 MB)
                    dst = os.path.join(outdir, f)
                    subprocess.run([ffmpeg, "-v", "error", "-y", "-i", src, "-vf", "scale=1920:-2", "-q:v", "2", dst], check=True)
                    if os.path.exists(drx): shutil.copy2(drx, dst[:-4] + ".drx")
                    rec = ""
                    if os.path.exists(drx):
                        m = re.search(r"<RecTC>([^<]*)</RecTC>", open(drx, errors="ignore").read()); rec = m.group(1) if m else ""
                    man.write("\t".join([pname, aname, f, rec, h]) + "\n"); man.flush(); new += 1
                log("  copied", new, "dupes skipped", dup)
                for path in sorted(Path(tmp).rglob("*")):
                    log("Removing temporary entry:", path)
                log("Removing temporary folder:", tmp)
                shutil.rmtree(tmp)
    log("DONE", job)


def main(args):
    if args == ["--configured"]:
        jobs = configured_jobs()
    elif len(args) >= 2:
        jobs = [(args[0], args[1:])]
    else:
        raise SystemExit(__doc__)
    if not jobs:
        return 0
    for job, _ in jobs:
        if Path(job).name != job or job in ("", ".", ".."):
            raise SystemExit("Each archive job must be a single directory name")

    sys.path.append(str(setting_path("resolve_modules")))
    import DaVinciResolveScript as dvr
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from resolve_busy import HeIsWorking, refuse_if_he_is_working

    dest = setting_path("gallery_share")
    if not dest.is_dir():
        raise SystemExit(f"Mount the archive at {dest} before exporting")
    scratch = setting_path("gallery_tmp")
    scratch.mkdir(parents=True, exist_ok=True)
    ffmpeg = str(setting_path("ffmpeg"))

    def log(*parts):
        line = time.strftime("%H:%M:%S ") + " ".join(map(str, parts))
        print(line, flush=True)
        with (scratch / "log.txt").open("a") as output:
            print(line, file=output)

    resolve = dvr.scriptapp("Resolve")
    if not resolve:
        raise SystemExit("Open Resolve before exporting gallery albums")
    try:
        refuse_if_he_is_working(resolve, "this stills export")
    except HeIsWorking as error:
        log("REFUSE:", error)
        return 1
    pm = resolve.GetProjectManager()
    for job, names in jobs:
        export_job(pm, job, names, dest, scratch, ffmpeg, log)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
