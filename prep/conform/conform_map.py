"""Map target shots to a graded source timeline using the supplied frame correspondence."""
import sys, json, numpy as np
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hprep_config import cfg, setting_path

sys.path.append(str(setting_path("resolve_modules")))
import DaVinciResolveScript as dvr
r = dvr.scriptapp("Resolve"); p = r.GetProjectManager().GetCurrentProject()
if not p or p.GetName() != cfg("conform_project"):
    raise SystemExit("Open the project named in conform_project first")
T = {p.GetTimelineByIndex(i + 1).GetName(): p.GetTimelineByIndex(i + 1) for i in range(p.GetTimelineCount())}
gv = T[cfg("source_timeline")].GetItemListInTrack("video", 1)
G = [(k, it.GetLeftOffset(), it.GetLeftOffset() + it.GetDuration()) for k, it in enumerate(gv)]
src = np.load(setting_path("frame_map"))
fv = T[cfg("target_timeline")].GetItemListInTrack("video", 1)
starts = sorted({it.GetLeftOffset() for it in fv})
N = len(src)
gb = {a for _, a, _ in G}  # his cut points, in old-send frames
add = [f for f in range(1, N) if src[f] >= 0 and src[f - 1] >= 0 and src[f] == src[f - 1] + 1 and src[f] in gb]
starts2 = sorted(set(starts) | set(add))
def g_of(old):
    for k, a, b in G:
        if a <= old < b:
            return k
    return None
ends = starts2[1:] + [N]; shots = []
for a, b in zip(starts2, ends):
    votes = [g_of(int(src[f])) for f in range(a, b) if src[f] >= 0]
    votes = [v for v in votes if v is not None]
    k = max(set(votes), key=votes.count) if votes else None
    shots.append({"start": a, "end": b, "grouped": None if k is None else k + 1, "share": round(len([v for v in votes if v == k]) / (b - a), 2) if votes else 0})
setting_path("conform_dir").mkdir(parents=True, exist_ok=True)
json.dump({"starts": starts2, "added_from_his_blades": add, "shots": shots}, open(setting_path("conform_dir") / "conform_map.json", "w"), indent=0)
print("his blades carried:", add)
print("final shots:", len(shots), "| inherit a grade:", sum(1 for s in shots if s["grouped"]), "| new:", [ (i+1,s["start"],s["end"]) for i,s in enumerate(shots) if not s["grouped"]])
print("partial (<90% of frames from one source):", [(i+1,s["start"],s["end"],s["grouped"],s["share"]) for i,s in enumerate(shots) if s["grouped"] and s["share"]<0.9])
