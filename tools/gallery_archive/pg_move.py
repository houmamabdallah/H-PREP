#!/usr/bin/env python3
"""Archive and verify gallery stills for named projects.

--map reads project names and disk usage. Export writes JPG and DRX files.
--sweep converts each nonempty local DPX and records its size and digest.
--verify checks the archive. --free prints eligible DPX paths and a removal
command for the operator to review. Project database backups stay in place.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "prep"))
from hprep_config import cfg, setting_path

SHARE = setting_path("gallery_share")
LIB = setting_path("project_library")
STILLS = setting_path("stills_dir")
HERE = Path(__file__).resolve().parent
GiB = 1024 ** 3


def project_names() -> dict[str, str]:
    """uuid -> project name, read from each project's own database (read-only, immutable)."""
    out: dict[str, str] = {}
    for d in sorted(LIB.iterdir()) if LIB.is_dir() else []:
        db = d / "Project.db"
        if not db.is_file():
            continue
        try:
            c = sqlite3.connect(f"file:{db}?mode=ro&immutable=1", uri=True)
            pid, pname = c.execute("select SM_Project_id, ProjectName from SM_Project limit 1").fetchone()
            out[pid] = pname
            c.close()
        except Exception:
            continue
    return out


def folders() -> list[tuple[int, int, str, str]]:
    """(bytes, files, project name, uuid) per stills folder, biggest first."""
    names = project_names()
    rows = []
    for p in STILLS.iterdir() if STILLS.is_dir() else []:
        if not p.is_dir():
            continue
        kb = subprocess.run(["du", "-sk", str(p)], capture_output=True, text=True).stdout.split()
        n = len(list(p.iterdir()))
        rows.append((int(kb[0]) * 1024 if kb else 0, n, names.get(p.name, "(no local project)"), p.name))
    rows.sort(reverse=True)
    return rows


def show_map(csv: bool) -> int:
    rows = folders()
    if csv:
        print("gib\tfiles\tproject\tuuid")
        for b, n, name, u in rows:
            print(f"{b / GiB:.1f}\t{n}\t{name}\t{u}")
    else:
        print(f"{'GiB':>7} {'files':>6}  project")
        for b, n, name, u in rows:
            if b < GiB // 10:
                continue
            print(f"{b / GiB:7.1f} {n:6d}  {name}")
    print(f"\n{sum(r[0] for r in rows) / GiB:.1f} GiB in {len(rows)} folders, "
          f"{sum(r[1] for r in rows)} files")
    if STILLS.is_dir():
        print(f"{shutil.disk_usage(STILLS).free / GiB:.1f} GiB free on the stills disk")
    print("Name the job and its projects to move one: pg_move.py JOB \"PROJECT_A\" ...")
    return 0


def safe(name: str) -> str:
    """Filesystem-safe project folder, the same rule pg_export uses for album names."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_")


def file_md5(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_partial_jpg(path: Path) -> bool:
    return path.name.lower().endswith(".partial.jpg")


def _is_album_jpg(path: Path, jobdir: Path) -> bool:
    """True for an album export JPG. Sweep output under _dpx/ and a half-written
    *.partial.jpg are not pictures this count is allowed to see."""
    if _is_partial_jpg(path):
        return False
    rel = path.relative_to(jobdir)
    return bool(rel.parts) and rel.parts[0] != "_dpx"


def _jpg_missing(jobdir: Path, rel: str) -> bool:
    jpg = jobdir / rel
    if _is_partial_jpg(jpg):
        return True
    try:
        return not jpg.is_file() or jpg.stat().st_size == 0
    except OSError:
        return True


def _dpx_lines_without_jpg(jobdir: Path) -> list[str]:
    """DPX-manifest rows whose JPG is missing, empty, or still a *.partial.jpg."""
    man = jobdir / "_dpx_manifest.tsv"
    if not man.is_file():
        return []
    bad: list[str] = []
    for raw in man.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        parts = raw.split("\t")
        label = parts[1] if len(parts) > 1 else raw
        if len(parts) < 6 or _jpg_missing(jobdir, parts[4]):
            bad.append(label)
    return bad


def _manifest_rows(path: Path) -> list[list[str]]:
    if not path.is_file():
        return []
    rows = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if raw.strip():
            rows.append(raw.split("\t"))
    return rows


def _row_covers(row: list[str], project: str, dpx: Path, size: int, digest: str, jobdir: Path) -> bool:
    """A manifest line covers this DPX when project, name, bytes and md5 match and the JPG has bytes."""
    if len(row) < 6:
        return False
    if row[0] != project or row[1] != dpx.name or row[2] != str(size) or row[3] != digest:
        return False
    return not _jpg_missing(jobdir, row[4])


def _dpx_files(folder: Path) -> list[Path]:
    return sorted(
        (p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".dpx"),
        key=lambda p: p.name,
    )


def _other_files(folder: Path) -> list[Path]:
    return [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() != ".dpx"]


def _album_export_present(jobdir: Path) -> bool:
    if not jobdir.is_dir():
        return False
    if (jobdir / "_manifest.tsv").is_file():
        return True
    return any(_is_album_jpg(p, jobdir) for p in jobdir.rglob("*.jpg"))


def _append_manifest(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
        fh.flush()


def remove_partial(path: Path) -> None:
    if path.exists():
        print(f"Removing temporary file: {path}", flush=True)
        path.unlink()


def _convert_to(src: Path, final: Path) -> bool:
    """Write final as a JPEG. The temp name keeps a .jpg suffix.

    oiiotool, sips and ffmpeg all pick the container from the output suffix.
    `stem.jpg.partial` is not a JPEG, so every real conversion fails; `stem.partial.jpg`
    in the same folder converts, and is renamed onto stem.jpg only after exit 0 and
    a file that has bytes (measured 2026-11-21). A configured sweep converter replaces the
    chain with `<cmd> <in> <out>` so a test can stand in for the three tools.
    """
    final.parent.mkdir(parents=True, exist_ok=True)
    tmp = final.with_name(final.stem + ".partial.jpg")
    remove_partial(tmp)
    override = cfg("sweep_converter") != "auto"
    if override:
        commands = [[str(setting_path("sweep_converter")), str(src), str(tmp)]]
    else:
        commands = [
            ["oiiotool", str(src), "--resize", "1920x0", "-o", str(tmp)],
            ["sips", "-s", "format", "jpeg", "--resampleWidth", "1920", str(src), "--out", str(tmp)],
            [str(setting_path("ffmpeg")), "-y", "-i", str(src), "-vf", "scale=1920:-2", "-q:v", "2", str(tmp)],
        ]
    for cmd in commands:
        remove_partial(tmp)
        try:
            proc = subprocess.run(cmd, capture_output=True, stdin=subprocess.DEVNULL)
        except OSError:
            if override:
                break
            continue
        if proc.returncode == 0 and tmp.is_file() and tmp.stat().st_size > 0:
            os.replace(tmp, final)
            return final.is_file() and final.stat().st_size > 0
        remove_partial(tmp)
        if override:
            break
    remove_partial(tmp)
    return False


def verify(job: str) -> int:
    jobdir = SHARE / job
    if not jobdir.is_dir():
        print(f"NOTHING ON THE SHARE for {job} ({jobdir})")
        return 1
    man = jobdir / "_manifest.tsv"
    lines = sum(1 for _ in open(man)) if man.is_file() else 0
    # Sweep JPGs live under _dpx/ and must not change this album count. A leftover
    # *.partial.jpg is not a converted picture either.
    jpg = [p for p in jobdir.rglob("*.jpg") if _is_album_jpg(p, jobdir)]
    drx = [p for p in jobdir.rglob("*.drx")]
    empty = [p for p in jpg if p.stat().st_size == 0]
    size = sum(p.stat().st_size for p in jpg) + sum(p.stat().st_size for p in drx)
    print(f"{job}: {len(jpg)} jpg, {len(drx)} drx, {lines} manifest lines, {size / GiB:.2f} GiB on the share")
    if empty:
        print(f"  {len(empty)} ZERO-BYTE jpg — not verified, do not free anything")
        return 1
    if not jpg:
        print("  NOTHING EXPORTED — an empty job folder is not a verified one. Check the export log: "
              f"{setting_path('gallery_tmp') / 'log.txt'}")
        return 1
    if lines != len(jpg):
        print(f"  manifest and files disagree ({lines} vs {len(jpg)}) — re-run the export, it is resumable")
        return 1
    bad = _dpx_lines_without_jpg(jobdir)
    if bad:
        print(f"  {len(bad)} dpx jpg missing or empty — not verified, do not free anything")
        for name in bad[:10]:
            print(f"    {name}")
        return 1
    print("  VERIFIED: every manifest line has a file with bytes in it.")
    return 0


def free(job: str, projects: list[str]) -> int:
    """Print the removal command for the local DPX of the named projects. Never runs it.

    The command is a list of DPX paths, never a folder. Every .dpx has to have a line in
    _dpx_manifest.tsv with this project, this name, the byte size and the md5 computed now,
    and that line's JPG has to exist with bytes. A .dpx of exactly 0 bytes is listed with
    no picture, because there is nothing in it to keep. A file of 1 byte or more with no
    picture still refuses. Project.db.* files are backups and stay.
    """
    names = {v: k for k, v in project_names().items()}
    jobdir = SHARE / job
    rows = _manifest_rows(jobdir / "_dpx_manifest.tsv")
    missing: list[str] = []
    dpx_paths: list[Path] = []
    stays: list[tuple[str, int, int]] = []
    empty_n = 0
    for pname in projects:
        u = names.get(pname)
        if not u:
            print(f"  no project named {pname!r} in the local database — skipped")
            continue
        folder = STILLS / u
        if not folder.is_dir():
            continue
        others = _other_files(folder)
        stays.append((pname, len(others), sum(p.stat().st_size for p in others)))
        for dpx in _dpx_files(folder):
            size = dpx.stat().st_size
            if size == 0:
                dpx_paths.append(dpx)
                empty_n += 1
                continue
            digest = file_md5(dpx)
            if any(_row_covers(row, pname, dpx, size, digest, jobdir) for row in rows):
                dpx_paths.append(dpx)
            else:
                missing.append(dpx.name)
    if missing:
        print(f"{len(missing)} DPX files have no picture on the share")
        for name in missing[:10]:
            print(f"  {name}")
        return 1
    # An album export still has to verify. Sweep-only (no _manifest.tsv, no album JPG)
    # has nothing for that check to look at; the DPX lines above are the gate.
    if _album_export_present(jobdir) and verify(job) != 0:
        print("\nNot printing a removal command: the share copy is not verified.")
        return 1
    if not dpx_paths:
        print("Nothing local to free for those projects.")
        return 0
    tmp = setting_path("gallery_tmp")
    tmp.mkdir(parents=True, exist_ok=True)
    list_path = tmp / f"{job}_free_list.txt"
    # The operator reviews this list before running the command.
    list_path.write_text("".join(f"{p}\n" for p in dpx_paths), encoding="utf-8")
    for path in dpx_paths:
        print(path)
    total = sum(p.stat().st_size for p in dpx_paths)
    if empty_n:
        print(f"{empty_n} empty DPX files (0 bytes) are in the list: nothing in them to keep.")
    print(f"\nThis frees {total / GiB:.1f} GiB. Your hands, not mine — run:\n")
    print(f"  tr '\\n' '\\0' < \"{list_path}\" | xargs -0 rm --")
    for pname, n, nbytes in stays:
        print(f"  {pname}: {n} non-DPX files stay, {nbytes / GiB:.1f} GiB")
    print("Project.db.* files are Resolve project backups and stay.")
    print("\nThe grades themselves live in the project database, not in these files; the share now holds the "
          "picture and the .drx for each still.")
    return 0


def sweep(job: str, projects: list[str]) -> int:
    """A 1920-wide JPG on the share for every local DPX. No Resolve, and nothing is deleted.

    Appends one TSV line per converted file:
    project, dpx name, dpx bytes, dpx md5, jpg path relative to the job, jpg bytes.
    The same name, bytes and md5 with a JPG that still has bytes is skipped.
    A .dpx of exactly 0 bytes is not converted: it counts as empty, not failed, and gets no manifest line.
    """
    if not projects:
        print("Name the projects to sweep: pg_move.py --sweep JOB \"PROJECT_A\" ...")
        return 1
    if not SHARE.is_dir():
        print(f"THE SHARE IS NOT MOUNTED ({SHARE}).")
        print("Mount the archive at the configured gallery_share path, then run again.")
        return 2
    names = {v: k for k, v in project_names().items()}
    jobdir = SHARE / job
    man_path = jobdir / "_dpx_manifest.tsv"
    any_failed = False
    for pname in projects:
        u = names.get(pname)
        if not u:
            print(f"  no project named {pname!r} in the local database — skipped")
            continue
        folder = STILLS / u
        if not folder.is_dir():
            print(f"{pname}: 0 converted, 0 skipped, 0 empty, 0 failed")
            continue
        rows = _manifest_rows(man_path)
        converted = skipped = empty = failed = 0
        outdir = jobdir / "_dpx" / safe(pname)
        for dpx in _dpx_files(folder):
            size = dpx.stat().st_size
            if size == 0:
                empty += 1
                continue
            digest = file_md5(dpx)
            final = outdir / f"{dpx.stem}.jpg"
            if any(_row_covers(row, pname, dpx, size, digest, jobdir) for row in rows):
                partial = final.with_name(final.stem + ".partial.jpg")
                remove_partial(partial)
                skipped += 1
                continue
            if not _convert_to(dpx, final):
                print(f"  failed {dpx.name}")
                failed += 1
                continue
            rel = final.relative_to(jobdir).as_posix()
            line = "\t".join([pname, dpx.name, str(size), digest, rel, str(final.stat().st_size)])
            _append_manifest(man_path, line)
            rows.append(line.split("\t"))
            converted += 1
        print(f"{pname}: {converted} converted, {skipped} skipped, {empty} empty, {failed} failed")
        if failed:
            any_failed = True
    return 1 if any_failed else 0


def resolve_project_open() -> str | None:
    """Return the current Resolve project before switching projects."""
    try:
        sys.path.insert(0, str(HERE.parent))
        sys.path.append(str(setting_path("resolve_modules")))
        import DaVinciResolveScript as dvr  # noqa: PLC0415

        from resolve_busy import open_project  # noqa: PLC0415
        r = dvr.scriptapp("Resolve")
        return open_project(r) if r else None
    except Exception:
        return None


def export(job: str, projects: list[str], his_resolve: bool = False) -> int:
    if his_resolve:
        # His yes, for this one run. pg_export's own guard reads the same variable, so the rule stays single.
        os.environ["RESOLVE_MAY_SWITCH"] = "1"
    open_now = resolve_project_open()
    if open_now and not his_resolve:
        print(f"RESOLVE HAS {open_now!r} OPEN. This export loads projects one after another, which would")
        print("switch him out of it mid-grade. Nothing has run.")
        print("Run it when he is out of Resolve, or pass --his-resolve if he has said it is fine to switch.")
        return 2
    if not SHARE.is_dir():
        print(f"THE SHARE IS NOT MOUNTED ({SHARE}).")
        print("Mount the archive at the configured gallery_share path, then run again.")
        return 2
    scratch = setting_path("gallery_tmp")
    scratch.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(scratch).free
    if free < 10 * GiB:
        print(f"Only {free / GiB:.1f} GiB free. The export writes one album of full-size stills into "
              f"{setting_path('gallery_tmp')} at a time; clear space before starting.")
        return 2
    if free < 25 * GiB:
        print(f"{free / GiB:.1f} GiB free — enough for one album at a time, which is all the export holds, "
              f"but watch it on a job with a thousand stills in one album.")
    cmd = [sys.executable, str(HERE / "pg_export.py"), job, *projects]
    print("running:", " ".join(cmd))
    r = subprocess.run(cmd)
    if r.returncode != 0:
        print("export did not finish cleanly — it is resumable, run it again")
        return r.returncode
    return verify(job)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("job", nargs="?")
    ap.add_argument("projects", nargs="*")
    ap.add_argument("--map", action="store_true")
    ap.add_argument("--csv", action="store_true")
    ap.add_argument("--sweep", metavar="JOB")
    ap.add_argument("--verify", metavar="JOB")
    ap.add_argument("--free", metavar="JOB")
    ap.add_argument("--his-resolve", action="store_true",
                    help="he has said it is fine to switch the project Resolve has open")
    a = ap.parse_args()

    # --sweep and --free take JOB as the flag value, so the first project is the
    # positional job and the rest are projects. `projects` alone drops that first name.
    named = ([a.job] if a.job else []) + list(a.projects)
    job = a.sweep or a.verify or a.free or a.job
    if job is not None and (Path(job).name != job or job in ("", ".", "..")):
        ap.error("Each archive job must be a single directory name")
    if a.map:
        return show_map(a.csv)
    if a.sweep:
        return sweep(a.sweep, named)
    if a.verify:
        return verify(a.verify)
    if a.free:
        return free(a.free, named)
    if a.job and a.projects:
        return export(a.job, a.projects, a.his_resolve)
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
