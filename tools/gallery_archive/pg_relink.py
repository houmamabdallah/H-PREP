#!/usr/bin/env python3
"""Find missing gallery names from the local project database. Use --link PROJECT_A
or --link-all to restore hard links. --unlink PROJECT_A removes verified twins.
With no arguments the tool only reports. --free estimates disk usage."""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "prep"))
from hprep_config import setting_path
HERE = Path(__file__).resolve().parent
GAL = setting_path("gallery_dir")
BACK = setting_path("stills_dir")
LIB = setting_path("project_library")
GiB = 1024 ** 3


def open_project() -> str | None:
    """The project Resolve has open, through the one check the repo shares."""
    try:
        sys.path.insert(0, str(HERE.parent))
        sys.path.append(str(setting_path("resolve_modules")))
        import DaVinciResolveScript as dvr  # noqa: PLC0415

        from resolve_busy import open_project as op  # noqa: PLC0415
        r = dvr.scriptapp("Resolve")
        return op(r) if r else None
    except Exception:
        return None


def projects() -> list[dict]:
    """Every project with gallery stills: its id, name, and where each still's file actually is."""
    out = []
    for d in sorted(LIB.iterdir()) if LIB.is_dir() else []:
        db = d / "Project.db"
        if not db.is_file():
            continue
        try:
            c = sqlite3.connect(f"file:{db}?mode=ro&immutable=1", uri=True)
            pid, pname = c.execute("select SM_Project_id, ProjectName from SM_Project limit 1").fetchone()
            rows = c.execute('select "Gallery::GyStill_id", GalleryPath from "Gallery::GyStill"').fetchall()
            c.close()
        except Exception:
            continue
        if not rows:
            continue
        seen, here, there, nowhere = [], 0, 0, []
        for sid, gp in rows:
            f = f"{sid}.dpx"
            g = GAL / pid / f
            b = BACK / pid / f
            # the still's own row says where Resolve will look; honour it rather than assuming
            root = Path(gp) if gp else GAL
            g = root / pid / f
            if g.exists():
                here += 1
            elif b.exists():
                there += 1
                seen.append((b, g))
            else:
                nowhere.append(f)
        out.append({"id": pid, "name": pname, "rows": len(rows), "visible": here,
                    "linkable": there, "lost": nowhere, "pairs": seen})
    out.sort(key=lambda r: -r["rows"])
    return out


def report(rows: list[dict]) -> int:
    busy = open_project()
    print(f"{'stills':>7} {'Resolve':>8} {'needs':>7}  project")
    print(f"{'in db':>7} {'sees':>8} {'a name':>7}")
    tot_need = tot_lost = 0
    for r in rows:
        tot_need += r["linkable"]
        tot_lost += len(r["lost"])
        flag = "  <- OPEN IN RESOLVE" if busy and r["name"] == busy else ""
        print(f"{r['rows']:>7} {r['visible']:>8} {r['linkable']:>7}  {r['name']}{flag}")
    print(f"\n{tot_need} stills are on this Mac under a name Resolve does not read — a directory entry away.")
    if tot_lost:
        print(f"{tot_lost} stills were absent from the checked paths. Check the archive mount.")
    if busy:
        print(f"\nResolve has {busy!r} open — --link-all will skip it. Nothing has been changed.")
    print("\nLink one job:  pg_relink.py --link \"PROJECT_A\"")
    return 0


def info_txt(pid: str, pname: str) -> None:
    """Resolve writes a little Info.txt beside each project's stills; match it so the folder looks native."""
    p = GAL / pid / "Info.txt"
    if p.exists():
        return
    p.write_text(f"Database Name: Local Database\nUser Name: guest\nProject Name: {pname}\n")


def link(rows: list[dict], name: str | None, every: bool) -> int:
    busy = open_project()
    targets = [r for r in rows if r["linkable"] and (every or r["name"] == name)]
    if not targets:
        if name:
            match = [r for r in rows if r["name"] == name]
            if match:
                print(f"{name}: nothing to link — Resolve already sees "
                      f"{match[0]['visible']} of {match[0]['rows']} stills.")
                return 0
            print(f"No project named {name!r}. Run with no arguments for the list.")
            return 1
        print("Every project's stills are already where Resolve looks.")
        return 0

    done = skipped = failed = 0
    for r in targets:
        if busy and r["name"] == busy:
            print(f"SKIPPING {r['name']} — open in Resolve. Its stills stay as they are.")
            skipped += 1
            continue
        (GAL / r["id"]).mkdir(parents=True, exist_ok=True)
        info_txt(r["id"], r["name"])
        made = 0
        for src, dst in r["pairs"]:
            if dst.exists():
                continue
            try:
                os.link(src, dst)  # one more name for the same bytes; nothing is copied or moved
                made += 1
            except OSError as e:
                print(f"  {dst.name}: {e}")
                failed += 1
        # prove it by inode rather than by the absence of an error
        ok = sum(1 for src, dst in r["pairs"]
                 if dst.exists() and dst.samefile(src))
        print(f"{r['name']}: {made} names added, {ok} of {len(r['pairs'])} now the same file as the original")
        done += made
    print(f"\n{done} stills are now visible to Resolve. 0 bytes copied — each is one file with two names.")
    if skipped:
        print(f"{skipped} project(s) skipped because they are open.")
    if failed:
        print(f"{failed} failed; nothing was lost, the originals are untouched.")
    print("Reverse any of it with --unlink \"PROJECT_A\".")
    return 0


def unlink(rows: list[dict], name: str) -> int:
    r = next((x for x in rows if x["name"] == name), None)
    if not r:
        print(f"No project named {name!r}.")
        return 1
    gallery = GAL / r["id"]
    backups = BACK / r["id"]
    if gallery.is_dir() and backups.is_dir() and gallery.samefile(backups):
        print("Gallery and backup directories are the same. Keeping every file.")
        return 1
    def has_twin(path):
        twin = backups / path.name
        return path.is_file() and twin.is_file() and not twin.is_symlink() and path.samefile(twin)

    paths = sorted(gallery.glob("*.dpx"))
    candidates = [path for path in paths if has_twin(path)]
    for path in candidates:
        print(f"Removing gallery link: {path}", flush=True)
    gone = 0
    for path in candidates:
        if has_twin(path):
            path.unlink()
            gone += 1
    held = len(paths) - gone
    print(f"{name}: {gone} .gallery names removed, {held} kept because that was the still's only name.")
    print("No picture was deleted: every removed name had a twin pointing at the same bytes.")
    return 0


def free(rows: list[dict], name: str) -> int:
    """What deleting this job's stills would REALLY free — a name is not a file."""
    r = next((x for x in rows if x["name"] == name), None)
    if not r:
        print(f"No project named {name!r}.")
        return 1
    seen: dict[int, int] = {}
    names: dict[int, int] = {}
    for root in (GAL / r["id"], BACK / r["id"]):
        for f in root.glob("*.dpx") if root.is_dir() else []:
            st = f.stat()
            seen[st.st_ino] = st.st_size
            names[st.st_ino] = st.st_nlink
    total = sum(seen.values())
    print(f"{name}: {len(seen)} actual files, {total / GiB:.2f} GiB of bytes.")
    print(f"They hold {sum(names.values())} names between them, so a folder's size is not what deleting it "
          f"frees — removing one of two names frees nothing at all.")
    print(f"Deleting BOTH names of all of them frees {total / GiB:.2f} GiB, and only after the share copy "
          f"verifies. Review the removal command before running it; pg_move.py --free prints it.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--link", metavar="PROJECT")
    ap.add_argument("--link-all", action="store_true")
    ap.add_argument("--unlink", metavar="PROJECT")
    ap.add_argument("--free", metavar="PROJECT")
    a = ap.parse_args()

    if not GAL.is_dir():
        print(f"{GAL} does not exist — Resolve has never written a still on this Mac.")
        return 1
    rows = projects()
    if a.unlink:
        return unlink(rows, a.unlink)
    if a.free:
        return free(rows, a.free)
    if a.link or a.link_all:
        return link(rows, a.link, a.link_all)
    return report(rows)


if __name__ == "__main__":
    sys.exit(main())
