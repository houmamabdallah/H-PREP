"""Auto-grouping for prep: shots of the same angle / setup that look alike -> one colour group.

Per shot, three frames (1/4, 1/2, 3/4) are read from the source file with ffmpeg at 192x108 and described by:
  picture - a DINOv2-small image embedding of the middle frame (what is in frame and how it's framed: the angle/setup)
  layout  - 32x18 luma thumbnail, normalised
  colour  - mean and spread of Lab, an 8x8 a/b histogram (same light = same setup)
  tone    - 16-bin L histogram
Distance = picture + 0.25 layout + 0.1 colour + 0.1 tone, average-linkage clustering cut at THRESH.
Without the embedding model the comparison uses layout, colour and tone.
The configured Python environment runs the frame analysis.

    prep_tree.py group          -> writes groups json + a contact sheet per group for review, changes nothing in Resolve
    prep_tree.py group apply    -> duplicates the current timeline as <name>_GROUPED and makes the colour groups there
"""
import os, json, subprocess, time
from hprep_config import setting_path
import numpy as np

FF = str(setting_path("ffmpeg"))
W, H = 192, 108
W_PIC, W_LAYOUT, W_COLOUR, W_TONE = 1.0, 0.25, 0.1, 0.1
THRESH = 0.78



def grab(path, frame, fps):
    r = subprocess.run([FF, "-v", "error", "-ss", "%.4f" % (frame / fps), "-i", path, "-frames:v", "1", "-vf", "scale=%d:%d" % (W, H),
                        "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True)
    if len(r.stdout) < W * H * 3:
        return None
    return np.frombuffer(r.stdout[:W * H * 3], np.uint8).reshape(H, W, 3)


def lab(rgb):
    x = rgb.astype(float) / 255
    x = np.where(x > 0.04045, ((x + 0.055) / 1.055) ** 2.4, x / 12.92)
    M = np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]])
    xyz = x @ M.T / np.array([0.9505, 1, 1.089])
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], -1)


def describe(path, a, b, fps):
    """a, b = source frame range of the shot (b exclusive)."""
    n = max(b - a, 1)
    imgs = [grab(path, a + int(n * q), fps) for q in (0.25, 0.5, 0.75)]
    imgs = [i for i in imgs if i is not None]
    if not imgs:
        return None
    L = np.concatenate([lab(i).reshape(-1, 3) for i in imgs])
    hab = np.histogram2d(L[:, 1], L[:, 2], bins=8, range=[[-40, 40], [-40, 40]])[0].ravel(); hab /= hab.sum()
    hl = np.histogram(L[:, 0], bins=16, range=(0, 100))[0].astype(float); hl /= hl.sum()
    thumbs = []
    for i in imgs:
        g = (i.astype(float) @ [0.2126, 0.7152, 0.0722]).reshape(18, 6, 32, 6).mean((1, 3)).ravel()
        thumbs.append((g - g.mean()) / (g.std() + 1e-6))
    return {"mean": L.mean(0), "std": L.std(0), "hab": hab, "hl": hl, "thumbs": thumbs, "mid": imgs[len(imgs) // 2]}


def embed(feats):
    """Adds f["emb"] (unit vector) to each shot; False if the model isn't available."""
    try:
        import torch
        from transformers import AutoImageProcessor, AutoModel
        from PIL import Image
    except ImportError:
        return False
    proc = AutoImageProcessor.from_pretrained("facebook/dinov2-small"); m = AutoModel.from_pretrained("facebook/dinov2-small").eval()
    with torch.no_grad():
        for f in feats:
            o = m(**proc(images=Image.fromarray(f["mid"]), return_tensors="pt")).last_hidden_state[0]
            e = torch.cat([o[0], o[1:].mean(0)]).numpy(); f["emb"] = e / np.linalg.norm(e)
    return True


def distance(p, q):
    # layout: best match among the three frames each (a moving camera still lands on the same framing somewhere)
    lay = min(1 - float(np.dot(x, y)) / len(x) for x in p["thumbs"] for y in q["thumbs"])
    col = np.linalg.norm(p["mean"] - q["mean"]) / 12 + np.abs(p["hab"] - q["hab"]).sum() / 2
    tone = np.abs(p["hl"] - q["hl"]).sum() / 2
    if "emb" in p and "emb" in q:
        return W_PIC * (1 - float(np.dot(p["emb"], q["emb"]))) + W_LAYOUT * lay + W_COLOUR * col + W_TONE * tone
    return 0.6 * lay + 0.5 * col / 2 + 0.3 * tone  # fallback weights (no embedding)


def cluster(feats, thresh=None):
    """Average-linkage agglomerative clustering; returns a list of index lists, ordered by first appearance."""
    n = len(feats)
    if thresh is None:
        thresh = THRESH if all("emb" in f for f in feats) else 0.8
    D = np.zeros((n, n))
    for i in range(n):
        for j in range(i + 1, n):
            D[i, j] = D[j, i] = distance(feats[i], feats[j])
    groups = [[i] for i in range(n)]
    while len(groups) > 1:
        best = None
        for x in range(len(groups)):
            for y in range(x + 1, len(groups)):
                d = D[np.ix_(groups[x], groups[y])].mean()
                if best is None or d < best[0]:
                    best = (d, x, y)
        if best[0] > thresh:
            break
        _, x, y = best
        groups[x] = sorted(groups[x] + groups[y]); del groups[y]
    return sorted(groups, key=lambda g: g[0]), D


def agreement(pred, truth):
    """Pair agreement with a reference grouping: of all shot pairs, how many are together/apart in both."""
    lab_p = {i: k for k, g in enumerate(pred) for i in g}
    lab_t = {i: k for k, g in enumerate(truth) for i in g}
    idx = sorted(set(lab_p) & set(lab_t)); same = tot = tp = fp = fn = 0
    for a in range(len(idx)):
        for b in range(a + 1, len(idx)):
            sp = lab_p[idx[a]] == lab_p[idx[b]]; st = lab_t[idx[a]] == lab_t[idx[b]]
            same += sp == st; tot += 1; tp += sp and st; fp += sp and not st; fn += st and not sp
    return {"pairs_agree": same / max(tot, 1), "together_found": tp / max(tp + fn, 1), "together_correct": tp / max(tp + fp, 1)}


def sheet(feats, groups, names, path):
    from PIL import Image, ImageDraw
    cols = max(len(g) for g in groups); tw, th = W, H
    im = Image.new("RGB", (160 + cols * (tw + 4), len(groups) * (th + 6)), (12, 12, 12)); d = ImageDraw.Draw(im)
    for r, (g, nm) in enumerate(zip(groups, names)):
        y = r * (th + 6)
        d.text((6, y + th // 2 - 6), nm, fill=(230, 230, 230))
        for c, i in enumerate(g):
            if i in feats:
                im.paste(Image.fromarray(feats[i]["mid"]), (160 + c * (tw + 4), y))
    im.save(path, quality=88)


def compute(shots, out_json, sheet_path):
    """shots: [{"path","a","b","fps","label"}] -> {"groups": [[index...]], "embedding": bool}. Runs in the venv."""
    feats, ok = [], []
    for i, s in enumerate(shots):
        f = describe(s["path"], s["a"], s["b"], s["fps"])
        if f is not None:
            feats.append(f); ok.append(i)
    emb = embed(feats)
    groups, _ = cluster(feats)
    groups = [[ok[i] for i in g] for g in groups]
    sheet({ok[i]: feats[i] for i in range(len(ok))}, groups, ["G%02d" % (k + 1) for k in range(len(groups))], sheet_path)
    json.dump({"groups": groups, "embedding": emb, "unreadable": [i for i in range(len(shots)) if i not in ok]}, open(out_json, "w"))


def run(ctx, apply=False):
    from prep_tree import real_items, label
    tl = ctx.tl
    items = real_items(tl)
    if apply and "GROUPED" in tl.GetName().upper():
        return ctx.say("This is already a GROUPED timeline. Run 'group apply' from the edit timeline; it makes a new GROUPED copy.")
    out = os.path.join(setting_path("state_dir"), ctx.p.GetName()); os.makedirs(out, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    shots = []
    for it in items:
        m = it.GetMediaPoolItem()
        shots.append({"path": m.GetClipProperty("File Path"), "a": int(round(it.GetLeftOffset())), "b": int(round(it.GetLeftOffset())) + int(it.GetDuration()),
                      "fps": float(m.GetClipProperty("FPS") or 25), "label": label(it)})
    sj, rj, sp = (os.path.join(out, "groups_%s.%s" % (stamp, x)) for x in ("shots.json", "json", "jpg"))
    json.dump(shots, open(sj, "w"))
    py = str(setting_path("group_python"))
    r = subprocess.run([py, os.path.abspath(__file__), sj, rj, sp], capture_output=True, text=True)
    if r.returncode or not os.path.exists(rj):
        return ctx.say("STOP: grouping failed:", r.stderr[-800:])
    res = json.load(open(rj)); groups = res["groups"]
    base = "".join(ch for ch in tl.GetName().upper() if ch.isalnum())[:10]
    names = ["%s_G%02d" % (base, k + 1) for k in range(len(groups))]
    ctx.say("GROUPS: %d shots -> %d groups, %d of them single shots (%s). Review sheet, one row per group: %s" % (
        len(shots), len(groups), sum(len(g) == 1 for g in groups), "picture embedding" if res["embedding"] else "FALLBACK: no embedding model", sp))
    for nm, g in zip(names, groups):
        ctx.say("  %-16s %s" % (nm, ", ".join(shots[i]["label"].split("@")[-1] for i in g)))
    for i in res["unreadable"]:
        ctx.say("  couldn't read frames:", shots[i]["label"])
    if not apply:
        return ctx.say("Nothing changed in Resolve. Run 'group apply' to make the GROUPED timeline with these groups.")
    new = tl.DuplicateTimeline(tl.GetName() + "_GROUPED")
    if not new:
        return ctx.say("STOP: couldn't duplicate the timeline.")
    ctx.p.SetCurrentTimeline(new)
    dup = real_items(new)
    if len(dup) != len(items):
        return ctx.say("STOP: the copy has %d shots, the edit %d. Groups not made." % (len(dup), len(items)))
    existing = {g.GetName(): g for g in ctx.p.GetColorGroupsList() or []}
    made = 0
    for nm, g in zip(names, groups):
        if len(g) < 2:
            continue  # a one-shot "group" is just a shot
        cg = existing.get(nm) or ctx.p.AddColorGroup(nm)
        for i in g:
            made += bool(dup[i].AssignToColorGroup(cg))
    ctx._saved = (new.GetName(), None, ctx._saved[2])  # stay on the new prep timeline
    ctx.say("GROUPED timeline '%s': %d shots assigned to %d groups." % (new.GetName(), made, sum(len(g) > 1 for g in groups)))


if __name__ == "__main__":
    import sys
    compute(json.load(open(sys.argv[1])), sys.argv[2], sys.argv[3])
