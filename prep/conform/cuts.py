"""Find cuts in a flattened colour send: decode 160x90 gray, frame-difference, plus a contact sheet (mid frame per shot).
usage: python3 cuts.py PLATE OUTDIR FPS"""
import sys, subprocess, json, os
import numpy as np
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hprep_config import setting_path
plate, out, fps = sys.argv[1], sys.argv[2], float(sys.argv[3])
os.makedirs(out, exist_ok=True)
W, H = 160, 90
p = subprocess.run([str(setting_path("ffmpeg")), "-v", "error", "-i", plate, "-vf", "scale=%d:%d" % (W, H), "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                   capture_output=True)
a = np.frombuffer(p.stdout, np.uint8).reshape(-1, H, W).astype(np.float32)
n = len(a)
d = np.abs(np.diff(a, axis=0)).mean(axis=(1, 2))
# cut where the difference is a local spike well above its neighbourhood
cuts = [0]
for i in range(len(d)):
    lo, hi = max(0, i - 6), min(len(d), i + 7)
    nb = np.delete(d[lo:hi], i - lo)
    if d[i] > 12 and d[i] > 3 * (np.median(nb) + 1) and i + 1 - cuts[-1] >= 4:
        cuts.append(i + 1)
json.dump({"frames": n, "fps": fps, "shot_starts": cuts}, open(os.path.join(out, "cuts.json"), "w"))
print("frames", n, "shots", len(cuts))
lens = np.diff(cuts + [n])
print("shot length frames: min %d median %d max %d" % (lens.min(), np.median(lens), lens.max()))
black = int((a.mean(axis=(1, 2)) < 6).sum())
print("near-black frames", black)
