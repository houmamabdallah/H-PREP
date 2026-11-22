"""cuts.py with OpenCV decode, for Macs without ffmpeg. Also writes a 960px frame every second and the mid frame of each shot.
usage: python3 cuts_cv.py PLATE OUTDIR  (not with -I or -s: cv2 lives in user site-packages)"""
import sys, json, os, cv2, numpy as np
plate, out = sys.argv[1], sys.argv[2]
c = cv2.VideoCapture(plate); fps = c.get(5); W, H = 160, 110
g = []; stats = []; i = 0
os.makedirs(out + "/frames", exist_ok=True)
while True:
    ok, f = c.read()
    if not ok: break
    s = cv2.resize(f, (W, H), interpolation=cv2.INTER_AREA)
    g.append(cv2.cvtColor(s, cv2.COLOR_BGR2GRAY).astype(np.float32))
    stats.append([float(x) for x in f.reshape(-1, 3)[::97].mean(0)] + [float(f.min()), float(f.max())])
    if i % 25 == 0: cv2.imwrite(out + "/frames/f%05d.jpg" % i, cv2.resize(f, (960, 659), interpolation=cv2.INTER_AREA))
    i += 1
a = np.array(g); n = len(a)
np.save(out + "/gray160.npy", a)
d = np.abs(np.diff(a, axis=0)).mean(axis=(1, 2))
cuts = [0]
for k in range(len(d)):
    lo, hi = max(0, k - 6), min(len(d), k + 7)
    nb = np.delete(d[lo:hi], k - lo)
    if d[k] > 12 and d[k] > 3 * (np.median(nb) + 1) and k + 1 - cuts[-1] >= 4:
        cuts.append(k + 1)
json.dump({"frames": n, "fps": fps, "shot_starts": cuts, "diff": d.tolist(), "stats_bgr_min_max": stats}, open(out + "/cuts.json", "w"))
lens = np.diff(cuts + [n])
print("frames", n, "shots", len(cuts), "len min/med/max", lens.min(), np.median(lens), lens.max())
# mid frames per shot
c = cv2.VideoCapture(plate)
for si, st in enumerate(cuts):
    m = st + lens[si] // 2
    c.set(cv2.CAP_PROP_POS_FRAMES, m); ok, f = c.read()
    if ok: cv2.imwrite(out + "/shot%03d_mid_f%05d.jpg" % (si + 1, m), cv2.resize(f, (960, 659), interpolation=cv2.INTER_AREA))
print("done")
