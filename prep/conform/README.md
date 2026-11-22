# Carry grades across a re-cut

1. Detect cuts in the new flattened send with `python3 prep/conform/cuts.py PLATE OUTDIR FPS`.
   The OpenCV alternative, `cuts_cv.py PLATE OUTDIR`, also saves mid-shot stills.
2. Supply a one-dimensional NumPy array at `frame_map`, with one element per new
   frame. Each value is the corresponding frame index in the old send, or -1
   for new material. The cut detectors produce shot boundaries. Frame
   correspondence is a separate input.
3. Open `conform_project`, set `source_timeline` to the graded version and
   `target_timeline` to a separate working copy of the re-cut. Both scripts use
   video track 1. Run `conform_map.py` to write `conform_map.json` in
   `conform_dir`. Review the match fractions and new shots. Prepare the target
   timeline with the listed cuts before carrying grades.
4. Export the source shots' gallery grades to a dedicated `drx` subdirectory
   of `conform_dir`. The DRX files need record timecodes. `carry_grades.py`
   uses these exports, clears accompanying DPX files in that folder, and
   applies mapped grades and groups to the target. It saves the project and
   records applied and skipped shots in `carry_grades.json`.
