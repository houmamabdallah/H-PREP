"""Apply previously exported source grades to matching shots on the target timeline."""
import sys, os, re, glob, json, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hprep_config import cfg, setting_path

sys.path.append(str(setting_path("resolve_modules")))
import DaVinciResolveScript as dvr
OUT = str(setting_path("conform_dir") / "drx")
r = dvr.scriptapp("Resolve"); pm = r.GetProjectManager(); p = pm.GetCurrentProject()
if not p or p.GetName() != cfg("conform_project"):
    raise SystemExit("Open the project named in conform_project first")
T = {p.GetTimelineByIndex(i + 1).GetName(): p.GetTimelineByIndex(i + 1) for i in range(p.GetTimelineCount())}
source_name = cfg("source_timeline")
target_name = cfg("target_timeline")
if source_name == target_name:
    raise SystemExit("Set target_timeline to a separate working copy")
G, F = T[source_name], T[target_name]
page = r.GetCurrentPage()
r.OpenPage("color"); p.SetCurrentTimeline(G); time.sleep(1)
gv = G.GetItemListInTrack("video", 1)
nominal = round(float(G.GetSetting("timelineFrameRate")))
def tc_to_frames(tc):
    h, m, s, f = [int(x) for x in re.split("[:;]", tc)]
    return ((h * 60 + m) * 60 + s) * nominal + f
gal = p.GetGallery()
if not glob.glob(OUT + "/*.drx"):
    raise SystemExit("no DRX exports: run the grab first")
for x in glob.glob(OUT + "/*.dpx"): os.remove(x)
pm.SaveProject()
drx = sorted(glob.glob(OUT + "/*.drx")); print("drx", len(drx))
# map DRX -> GROUPED item by record timecode
starts = {it.GetStart(): k for k, it in enumerate(gv)}
by_item = {}
for d in drx:
    m = re.search(r"<RecTC>([^<]+)</RecTC>", open(d, errors="ignore").read())
    if not m: continue
    rec = tc_to_frames(m.group(1))
    k = max((k for k, it in enumerate(gv) if it.GetStart() <= rec), default=None)
    if k is not None and rec < gv[k].GetEnd(): by_item[k] = d
print("mapped", len(by_item), "of", len(gv))
cm = json.load(open(setting_path("conform_dir") / "conform_map.json"))
p.SetCurrentTimeline(F); fv = F.GetItemListInTrack("video", 1); assert len(fv) == len(cm["shots"])
groups = {g.GetName(): g for g in p.GetColorGroupsList()}
done, skipped = [], []
for i, (it, s) in enumerate(zip(fv, cm["shots"])):
    if not s["grouped"]: skipped.append((i + 1, "new shot")); continue
    k = s["grouped"] - 1
    if it.GetNodeGraph().GetNumNodes() > 1: skipped.append((i + 1, "already graded")); continue
    d = by_item.get(k)
    ok = bool(d) and bool(it.GetNodeGraph().ApplyGradeFromDRX(d, 0))
    cg = gv[k].GetColorGroup()
    gok = it.AssignToColorGroup(groups[cg.GetName()]) if cg else None
    done.append((i + 1, k + 1, ok, cg.GetName() if cg else None, gok))
pm.SaveProject()
json.dump({"done": done, "skipped": skipped}, open(setting_path("conform_dir") / "carry_grades.json", "w"), indent=0)
print("applied", sum(1 for d in done if d[2]), "| failed", [d for d in done if not d[2]], "| grouped", sum(1 for d in done if d[4]), "| skipped", skipped)
