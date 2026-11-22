"""PREP TREE: one prep pass that leaves a job ready to grade.

    group     shots of the same setup that look alike -> Resolve colour groups, on a NEW "<timeline>_GROUPED" timeline
    template  save one shot's tree as the job's PREP TREE, or put it on every ungraded shot (same numbered nodes everywhere)
    spread    in every group: the ONE graded shot (the hero) -> its tree onto the group's ungraded shots
    look      a configured DCTL on every group's Post-Clip node: the whole film, one place
    attach    attach existing clip mattes from the configured folder
    status    what is grouped, graded, spread, looked and matted, per group

Timeline and grade handling:
- Groups, spreads and looks only on the PREP timeline (name contains GROUPED or PREP). Version / blend timelines stay unlinked.
- A clip that already has a grade is NEVER written to: no DRX on graded clips. Only empty clips (1 node, no tools) get a tree.
- No DRX on a group or timeline graph (that crashed Resolve on 2026-10-14). Group graphs only get SetLUT.
- Current timeline, playhead and page are put back after every step.

Run inside Resolve: Workspace > Scripts > "Prep - ..." (installed by prep/install.sh), or from a terminal:
    python3 prep_tree.py status|spread|template save NAME|template apply NAME|look NAME|attach|group [apply]
"""
import os, sys, time, glob
from pathlib import Path

from hprep_config import setting_path

HERE = os.path.dirname(os.path.abspath(__file__))
ALBUM = "_prep_templates"
WINDOW_TOOLS = ("Power Windows", "Tracker", "Magic Mask", "Qualifier", "Key", "Matte")


# ---------------------------------------------------------------- Resolve access
def connect(res=None):
    if res is not None:
        return res
    try:
        import DaVinciResolveScript as dvr
    except ImportError:
        sys.path.append(str(setting_path("resolve_modules")))
        import DaVinciResolveScript as dvr
    r = dvr.scriptapp("Resolve")
    if r is None:
        raise SystemExit("Resolve isn't reachable. Open it, and set Preferences > General > External scripting using = Local.")
    return r


class Ctx:
    """Project + timelines + a way to put the UI back exactly as it was."""
    def __init__(self, res):
        self.res = res
        self.p = res.GetProjectManager().GetCurrentProject()
        self.mp = self.p.GetMediaPool()
        self.tl = self.p.GetCurrentTimeline()
        self._saved = (self.tl.GetName() if self.tl else None, self.tl.GetCurrentTimecode() if self.tl else None, res.GetCurrentPage())
        self.log = []

    def timelines(self):
        return {self.p.GetTimelineByIndex(i).GetName(): self.p.GetTimelineByIndex(i) for i in range(1, self.p.GetTimelineCount() + 1)}

    def restore(self):
        name, tc, page = self._saved
        if name:
            t = self.timelines().get(name)
            if t:
                self.p.SetCurrentTimeline(t)
                if tc:
                    t.SetCurrentTimecode(tc)
        if page:
            self.res.OpenPage(page)

    def say(self, *a):
        s = " ".join(str(x) for x in a)
        self.log.append(s)
        print(s)

    def write_report(self, step):
        d = os.path.join(setting_path("state_dir"), self.p.GetName())
        os.makedirs(d, exist_ok=True)
        f = os.path.join(d, "%s_%s.txt" % (time.strftime("%Y%m%d_%H%M%S"), step))
        open(f, "w").write("\n".join(self.log) + "\n")
        return f


def is_prep(tl):
    n = (tl.GetName() if tl else "").upper()
    return "GROUPED" in n or "PREP" in n


def real_items(tl):
    """Video clips that are real shots: have media and more than one frame."""
    out = []
    for tr in range(1, tl.GetTrackCount("video") + 1):
        for it in tl.GetItemListInTrack("video", tr) or []:
            m = it.GetMediaPoolItem()
            if not m or it.GetDuration() < 2:
                continue
            try:
                if int(m.GetClipProperty("Frames") or 0) < 2:
                    continue
            except ValueError:
                pass
            if "OFFLINE" in (it.GetName() or "").upper() or "BOUNCE" in (it.GetName() or "").upper():
                continue  # the A/B offline reference on a top track is not a shot
            out.append(it)
    return out


def clip_state(it):
    """nodes, tools per node, graded (anything in the tree at all), own (windows / keys / tracking that belong to THIS shot)."""
    g = it.GetNodeGraph()
    n = g.GetNumNodes() if g else 0
    tools = [(g.GetToolsInNode(i) or []) for i in range(1, n + 1)]
    luts = [(g.GetLUT(i) or "") for i in range(1, n + 1)]
    graded = n > 1 or any(tools) or any(luts)
    own = sorted({t for ts in tools for t in ts if any(w in t for w in WINDOW_TOOLS)})
    return {"nodes": n, "tools": tools, "graded": graded, "own": own}


def frames_to_tc(f, fps):
    b = int(round(fps))
    return "%02d:%02d:%02d:%02d" % (f // (3600 * b), (f // (60 * b)) % 60, (f // b) % 60, f % b)


def tl_fps(tl):
    try:
        return float(tl.GetSetting("timelineFrameRate"))
    except (TypeError, ValueError):
        return 25.0


def label(it):
    return "%s @%s" % (it.GetName()[:40], it.GetStart())


# ---------------------------------------------------------------- grade files
def _album(ctx):
    gal = ctx.p.GetGallery()
    for a in gal.GetGalleryStillAlbums() or []:
        if gal.GetAlbumName(a) == ALBUM:
            gal.SetCurrentStillAlbum(a)
            return a
    a = gal.CreateGalleryStillAlbum()
    gal.SetAlbumName(a, ALBUM)
    gal.SetCurrentStillAlbum(a)
    return a


def export_grade(ctx, tl, it, out_dir, name):
    """Saves the grade of `it` as <out_dir>/<name>*.drx (grab still -> export DRX -> delete the still). Read-only on the clip."""
    os.makedirs(out_dir, exist_ok=True)
    for f in glob.glob(os.path.join(out_dir, name + "*")):
        os.remove(f)
    ctx.res.OpenPage("color")
    ctx.p.SetCurrentTimeline(tl)
    mid = int(it.GetStart() + it.GetDuration() // 2)
    tl.SetCurrentTimecode(frames_to_tc(mid, tl_fps(tl)))
    time.sleep(0.5)
    cur = tl.GetCurrentVideoItem()
    if not cur or cur.GetUniqueId() != it.GetUniqueId():
        raise RuntimeError("playhead didn't land on %s (is a higher track covering it?)" % label(it))
    alb = _album(ctx)
    st = tl.GrabStill()
    if not st:
        raise RuntimeError("GrabStill failed on " + label(it))
    alb.ExportStills([st], os.fspath(out_dir), name, "drx")
    alb.DeleteStills([st])
    for _ in range(40):
        got = sorted(glob.glob(os.path.join(out_dir, name + "*.drx")))
        if got and os.path.getsize(got[0]) > 1000:
            for x in glob.glob(os.path.join(out_dir, name + "*.dpx")):
                os.remove(x)  # the still image isn't needed, only the grade
            return got[0]
        time.sleep(0.25)
    raise RuntimeError("DRX export didn't appear for " + label(it))


def apply_grade(it, drx):
    """Only ever called on clips clip_state() says are empty."""
    return bool(it.GetNodeGraph().ApplyGradeFromDRX(drx, 0))


# ---------------------------------------------------------------- steps
def status(ctx):
    tl = ctx.tl
    ctx.say("PROJECT %s | TIMELINE %s %s" % (ctx.p.GetName(), tl.GetName(), "(prep)" if is_prep(tl) else "(not a prep timeline)"))
    items = real_items(tl)
    by_group = {}
    for it in items:
        g = it.GetColorGroup()
        by_group.setdefault(g.GetName() if g else None, []).append(it)
    for g in ctx.p.GetColorGroupsList() or []:
        mem = by_group.get(g.GetName(), [])
        if not mem:
            continue
        st = [clip_state(m) for m in mem]
        post = g.GetPostClipNodeGraph(); pre = g.GetPreClipNodeGraph()
        look = post.GetLUT(1) if post and post.GetNumNodes() else ""
        pre_used = any(pre.GetToolsInNode(i) or pre.GetLUT(i) for i in range(1, pre.GetNumNodes() + 1)) if pre else False
        ctx.say("  %-24s %2d shots | graded %2d | own windows/keys %d | pre-clip %s | look %s" % (
            g.GetName(), len(mem), sum(s["graded"] for s in st), sum(bool(s["own"]) for s in st),
            "graded" if pre_used else "empty", os.path.basename(look) or "-"))
    loose = by_group.get(None, [])
    if loose:
        ctx.say("  %-24s %2d shots (not in a group)" % ("UNGROUPED", len(loose)))
    srcs = {}
    for it in items:
        m = it.GetMediaPoolItem()
        srcs.setdefault(m.GetClipProperty("File Path"), m)
    for path, m in srcs.items():
        mattes = [os.path.basename(x) for x in (ctx.mp.GetClipMatteList(m) or [])]
        ctx.say("  MATTES %s: %s" % (os.path.basename(path), ", ".join(mattes) or "none attached"))
    ctx.say("  TEMPLATES: %s" % (", ".join(os.path.basename(x)[:-4] for x in sorted(glob.glob(os.path.join(setting_path("templates_dir"), "*.drx")))) or "none saved"))


def work_timeline(ctx, step):
    """Every write goes onto a NEW timeline (his rule, 2026-10-15): duplicate the current one as
    <name>_<STEP>_<MMDD-HHMM>, give the copy its OWN colour groups (<group>_<STEP><MMDD-HHMM>) so a group grade or look
    on the copy can't reach his timeline (groups are project-wide), and leave him on the copy. His timeline is untouched."""
    src = ctx.tl
    tag = "%s_%s" % (step.upper(), time.strftime("%m%d-%H%M"))
    base = src.GetName()
    new = src.DuplicateTimeline("%s_%s" % (base, tag))
    if not new:
        raise RuntimeError("couldn't duplicate %s" % base)
    ctx.p.SetCurrentTimeline(new)
    a, b = real_items(src), real_items(new)
    if len(a) != len(b):
        raise RuntimeError("the copy has %d shots, %s has %d" % (len(b), base, len(a)))
    groups = {g.GetName(): g for g in ctx.p.GetColorGroupsList() or []}
    moved = 0
    for o, d in zip(a, b):
        g = o.GetColorGroup()
        if not g:
            continue
        nm = "%s_%s" % (g.GetName(), tag)
        if nm not in groups:
            groups[nm] = ctx.p.AddColorGroup(nm)
        moved += bool(d.AssignToColorGroup(groups[nm]))
    ctx.tl = new
    ctx._saved = (new.GetName(), None, ctx._saved[2])  # stay on the new timeline so he sees the result
    ctx.say("NEW TIMELINE %s (copy of %s, %d shots in its own groups). %s is untouched." % (new.GetName(), base, moved, base))
    return new


def pick_hero(graded, st, cur):
    """The playhead's shot if it's one of them, else the most built tree (most nodes, then the longest shot)."""
    for m in graded:
        if cur and m.GetUniqueId() == cur.GetUniqueId():
            return m, "playhead"
    return max(graded, key=lambda m: (st[m.GetUniqueId()]["nodes"], m.GetDuration())), "most nodes"


def spread(ctx):
    """Per group: the hero's tree goes onto every EMPTY shot of the group, on a NEW timeline. Graded shots are never touched."""
    if not is_prep(ctx.tl):
        return ctx.say("STOP: '%s' isn't a prep timeline (name must contain GROUPED or PREP). Groups live in prep only." % ctx.tl.GetName())
    cur = ctx.tl.GetCurrentVideoItem()
    cur_pos = int(cur.GetStart()) if cur else None
    tl = work_timeline(ctx, "spread")
    if cur_pos is not None:  # the same shot on the copy
        cur = next((m for m in real_items(tl) if int(m.GetStart()) == cur_pos), None)
    work = os.path.join(setting_path("state_dir"), ctx.p.GetName(), "heroes")
    done = 0
    for g in ctx.p.GetColorGroupsList() or []:
        mem = [m for m in (g.GetClipsInTimeline(tl) or []) if m.GetDuration() > 1]
        if not mem:
            continue
        st = {m.GetUniqueId(): clip_state(m) for m in mem}
        graded = [m for m in mem if st[m.GetUniqueId()]["graded"]]
        empty = [m for m in mem if not st[m.GetUniqueId()]["graded"]]
        if not graded:
            ctx.say("  %-34s nothing graded yet" % g.GetName()); continue
        if not empty:
            ctx.say("  %-34s every shot already has a grade, nothing to spread" % g.GetName()); continue
        hero, why = pick_hero(graded, st, cur)
        if st[hero.GetUniqueId()]["own"]:
            ctx.say("  %-34s note: hero has %s; empty shots get a copy to re-track / swap" % (g.GetName(), ", ".join(st[hero.GetUniqueId()]["own"])))
        drx = export_grade(ctx, tl, hero, work, g.GetName())
        ok = [apply_grade(m, drx) for m in empty]
        done += sum(ok)
        ctx.say("  %-34s hero %s (%d nodes, %s%s) -> %d/%d empty shots%s" % (
            g.GetName(), label(hero), st[hero.GetUniqueId()]["nodes"], why, ", %d graded" % len(graded) if len(graded) > 1 else "",
            sum(ok), len(empty), "" if all(ok) else "  (FAILED on %d)" % ok.count(False)))
    ctx.say("SPREAD: %d shots got their group's tree on %s. Copy grades back to your own timeline when you want them. From here: grade the"
            " group in GROUP PRE-CLIP (live on every shot), or change a node and Color > Ripple Node Changes to Current Group." % (done, tl.GetName()))


def template(ctx, action, name):
    path = os.path.join(setting_path("templates_dir"), name + ".drx")
    tl = ctx.tl
    if action == "save":
        it = tl.GetCurrentVideoItem()
        if not it:
            return ctx.say("Park the playhead on the shot whose tree is the template.")
        f = export_grade(ctx, tl, it, setting_path("templates_dir"), name)
        if f != path:
            os.replace(f, path)
        return ctx.say("TEMPLATE saved: %s (%d nodes) from %s" % (path, clip_state(it)["nodes"], label(it)))
    if not os.path.exists(path):
        return ctx.say("No template %s. Saved ones: %s" % (name, [os.path.basename(x) for x in glob.glob(os.path.join(setting_path("templates_dir"), "*.drx"))]))
    if not is_prep(tl):
        return ctx.say("STOP: '%s' isn't a prep timeline." % tl.GetName())
    tl = work_timeline(ctx, "tree")
    n = skipped = 0
    for it in real_items(tl):
        if clip_state(it)["graded"]:
            skipped += 1; continue
        n += apply_grade(it, path)
    ctx.say("TEMPLATE %s -> %d empty shots; %d graded shots left alone." % (name, n, skipped))


def looks_directory():
    root = setting_path("lut_root").resolve()
    directory = setting_path("looks_dir").resolve()
    try:
        relative = directory.relative_to(root)
    except ValueError:
        raise SystemExit("Config key 'looks_dir' must be inside 'lut_root'") from None
    return root, relative


def looks_available():
    root, relative = looks_directory()
    return sorted(p.stem for p in (root / relative).glob("*.dctl") if p.is_file())


def look(ctx, name):
    tl = ctx.tl
    if not is_prep(tl):
        return ctx.say("STOP: '%s' isn't a prep timeline." % tl.GetName())
    root, directory = looks_directory()
    if name not in looks_available():
        return ctx.say("No look %s. In %s: %s" % (name, root / directory, looks_available()))
    tl = work_timeline(ctx, "look")
    rel = (directory / (name + ".dctl")).as_posix()
    for g in ctx.p.GetColorGroupsList() or []:
        if not g.GetClipsInTimeline(tl):
            continue
        post = g.GetPostClipNodeGraph()
        cur = post.GetLUT(1) or ""; tools = post.GetToolsInNode(1) or []
        if tools and (not cur or Path(cur).parent != directory):
            ctx.say("  %-24s post-clip node 1 holds %s: left alone" % (g.GetName(), tools)); continue
        ok = post.SetLUT(1, rel)
        ctx.say("  %-24s look %s %s" % (g.GetName(), name, "on" if ok else "FAILED"))
    ctx.say("LOOK: every group's Post-Clip node 1 = %s. Change it for the whole film by running this again with another look." % name)


# ---------------------------------------------------------------- mattes
def matte_files(path):
    stem = os.path.splitext(os.path.basename(path))[0]
    d = setting_path("mattes_dir")
    if not d.is_dir():
        return []
    packed = [os.path.join(d, f) for f in sorted(os.listdir(d)) if f.startswith(stem + "_") and f.endswith("MATTES.mov") and (d / f).is_file()]
    if packed:  # one RGBA file per selection (R skin, G face, B rest, A person): attach only these
        return packed
    fs = [os.path.join(d, "%s_%s.mov" % (stem, k)) for k in ("PERSON", "SKIN")]
    fs += sorted(os.path.join(d, f) for f in os.listdir(d)
                 if f.startswith(stem + "_P") and f.endswith(".mov") and f[len(stem) + 2:len(stem) + 3].isdigit())
    return [f for f in fs if os.path.isfile(f)]


def attach(ctx):
    ms = ctx.res.GetMediaStorage(); seen = set(); n = 0; missing = []
    for it in real_items(ctx.tl):
        m = it.GetMediaPoolItem(); path = m.GetClipProperty("File Path") or ""
        if not path or path in seen:
            continue
        seen.add(path)
        have = {os.path.basename(x) for x in (ctx.mp.GetClipMatteList(m) or [])}
        fs = [f for f in matte_files(path) if os.path.basename(f) not in have]
        if not fs and not have:
            missing.append(path); continue
        if fs:
            ok = ms.AddClipMattesToMediaPool(m, fs); n += len(fs) if ok else 0
            ctx.say("  %s %s" % ("attached" if ok else "FAILED", ", ".join(os.path.basename(f) for f in fs)))
    ctx.say("MATTES: %d attached; %d source(s) still without mattes" % (n, len(missing)))
    return missing


def attach_file(ctx, plate):
    """Attach the finished mattes of one source file to every media pool clip of it in the open project (any timeline)."""
    ms = ctx.res.GetMediaStorage(); n = 0
    def walk(f):
        for c in f.GetClipList() or []:
            yield c
        for s in f.GetSubFolderList() or []:
            yield from walk(s)
    fs = matte_files(plate)
    for c in walk(ctx.mp.GetRootFolder()):
        if c.GetClipProperty("File Path") != plate or int(c.GetClipProperty("Frames") or 0) < 2:
            continue
        have = {os.path.basename(x) for x in (ctx.mp.GetClipMatteList(c) or [])}
        new = [f for f in fs if os.path.basename(f) not in have]
        if new and ms.AddClipMattesToMediaPool(c, new):
            n += len(new)
    ctx.say("MATTES: %d attached for %s in %s" % (n, os.path.basename(plate), ctx.p.GetName())
            + ("" if n else ". Nothing to attach here: open the project that uses this plate and run Workspace > Scripts > Prep - Mattes."))


# ---------------------------------------------------------------- entry
def main(argv, res=None):
    if argv and argv[0] not in {"status", "spread", "template", "look", "attach", "attach-file", "group"}:
        raise SystemExit(__doc__)
    ctx = Ctx(connect(res))
    if not ctx.tl and (argv[:1] != ["attach-file"]):
        return print("Open the prep timeline first.")
    cmd = argv[0] if argv else "status"
    try:
        if cmd == "status":
            status(ctx)
        elif cmd == "spread":
            spread(ctx)
        elif cmd == "template":
            template(ctx, argv[1], argv[2] if len(argv) > 2 else "PREP_TREE")
        elif cmd == "look":
            look(ctx, argv[1])
        elif cmd == "attach":
            attach(ctx)
        elif cmd == "attach-file":
            attach_file(ctx, argv[1])
        elif cmd == "group":
            import autogroup
            autogroup.run(ctx, apply=len(argv) > 1 and argv[1] == "apply")
        else:
            print(__doc__)
    finally:
        ctx.restore()
        if ctx.log:
            print("report:", ctx.write_report(cmd))


if __name__ == "__main__":
    sys.path.insert(0, HERE)
    main(sys.argv[1:])
    sys.stdout.flush()
    os._exit(0)  # the external fusionscript module segfaults on interpreter teardown (exit 139); the work is already done
