#!/usr/bin/env python3
"""Sweep and free, against a fake library. No real stills, no Resolve.

The converter stand-in refuses an output whose name does not end in .jpg, which is
what oiiotool, sips and ffmpeg do: a temp named stem.jpg.partial is not a JPEG.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PG_MOVE = Path(__file__).resolve().parent / "pg_move.py"


class PgMoveSweepTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.stills = self.root / "stills"
        self.share = self.root / "share"
        self.lib = self.root / "lib"
        self.pg_tmp = self.root / "pg_tmp"
        for path in (self.stills, self.share, self.lib, self.pg_tmp):
            path.mkdir()
        self.project = "ALBUM TEST"
        self.uuid = "11111111-2222-3333-4444-555555555555"
        self._add_project(self.uuid, self.project)
        self.folder = self.stills / self.uuid
        self.folder.mkdir()
        self.conv_log = self.root / "convert.log"
        self.converter = self.root / "convert.py"
        self._write_converter()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _add_project(self, uuid: str, name: str) -> None:
        folder = self.lib / uuid
        folder.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(folder / "Project.db")
        try:
            con.execute("PRAGMA journal_mode=DELETE")
            con.execute("CREATE TABLE SM_Project (SM_Project_id TEXT, ProjectName TEXT)")
            con.execute(
                "INSERT INTO SM_Project (SM_Project_id, ProjectName) VALUES (?, ?)",
                (uuid, name),
            )
            con.commit()
        finally:
            con.close()

    def _write_converter(self) -> None:
        log = repr(str(self.conv_log))
        self.converter.write_text(
            "#!/usr/bin/python3\n"
            "import os, sys\n"
            "src, dst = sys.argv[1], sys.argv[2]\n"
            f"open({log}, 'a').write(src + '\\t' + dst + '\\n')\n"
            "if not os.path.basename(dst).lower().endswith('.jpg'):\n"
            "    sys.stderr.write('output suffix is not .jpg\\n')\n"
            "    sys.exit(1)\n"
            "data = open(src, 'rb').read(64)\n"
            "open(dst, 'wb').write(data)\n"
        )
        self.converter.chmod(0o755)

    def _env(self, convert: bool) -> dict[str, str]:
        env = os.environ.copy()
        config = self.root / "settings.json"
        config.write_text(json.dumps({
            "stills_dir": str(self.stills),
            "gallery_share": str(self.share),
            "project_library": str(self.lib),
            "gallery_tmp": str(self.pg_tmp),
            "sweep_converter": str(self.converter) if convert else "auto",
            "ffmpeg": shutil.which("ffmpeg") or "ffmpeg",
        }))
        env["HPREP_CONFIG"] = str(config)
        return env

    def _run(self, args: list[str], *, convert: bool = True, timeout: int = 60) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(PG_MOVE), *args],
            env=self._env(convert),
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    @staticmethod
    def _text(result: subprocess.CompletedProcess) -> str:
        return f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"

    def _manifest(self, job: str) -> list[list[str]]:
        path = self.share / job / "_dpx_manifest.tsv"
        return [line.split("\t") for line in path.read_text().splitlines() if line.strip()]

    def _plant_album(self, job: str, lines: int = 1) -> None:
        album = self.share / job / "Album"
        album.mkdir(parents=True, exist_ok=True)
        (album / "still.jpg").write_bytes(b"album-jpeg-bytes")
        text = "".join(f"proj\tAlbum\tstill.jpg\t\thash{i}\n" for i in range(lines))
        (self.share / job / "_manifest.tsv").write_text(text)

    def test_sweep_converts_then_second_sweep_skips(self) -> None:
        files = {
            "one.dpx": b"ONE-dpx-payload-0001",
            "two.dpx": b"TWO-dpx-payload-0002",
            "three.DPX": b"THREE-dpx-payload-003",
        }
        for name, data in files.items():
            (self.folder / name).write_bytes(data)
        (self.folder / "notes.txt").write_bytes(b"not a still")

        first = self._run(["--sweep", "JOB", self.project])
        self.assertEqual(first.returncode, 0, self._text(first))
        self.assertIn(f"{self.project}: 3 converted, 0 skipped, 0 empty, 0 failed", first.stdout)

        rows = {row[1]: row for row in self._manifest("JOB")}
        self.assertEqual(set(rows), set(files))
        for name, data in files.items():
            row = rows[name]
            self.assertEqual(row[0], self.project)
            self.assertEqual(row[2], str(len(data)))
            self.assertEqual(row[3], hashlib.md5(data).hexdigest())
            self.assertEqual(len(row), 6)
            jpg = self.share / "JOB" / row[4]
            self.assertTrue(jpg.is_file() and jpg.stat().st_size > 0, jpg)
            self.assertEqual(row[5], str(jpg.stat().st_size))
            self.assertTrue(row[4].endswith(".jpg"), row[4])
            self.assertFalse(row[4].endswith(".partial.jpg"), row[4])
            self.assertNotIn(".jpg.partial", row[4])
        self.assertEqual(rows["one.dpx"][4], "_dpx/ALBUM_TEST/one.jpg")
        self.assertEqual(rows["three.DPX"][4], "_dpx/ALBUM_TEST/three.jpg")
        self.assertNotIn("notes.txt", (self.share / "JOB" / "_dpx_manifest.tsv").read_text())

        calls = [line.split("\t", 1)[1] for line in self.conv_log.read_text().splitlines()]
        self.assertEqual(len(calls), 3)
        for dst in calls:
            self.assertTrue(dst.endswith(".partial.jpg"), dst)
            self.assertFalse(dst.endswith(".jpg.partial"), dst)
        self.assertEqual(list((self.share / "JOB").rglob("*partial*")), [])

        before = (self.share / "JOB" / "_dpx_manifest.tsv").read_text()
        second = self._run(["--sweep", "JOB", self.project])
        self.assertEqual(second.returncode, 0, self._text(second))
        self.assertIn(f"{self.project}: 0 converted, 3 skipped, 0 empty, 0 failed", second.stdout)
        self.assertEqual((self.share / "JOB" / "_dpx_manifest.tsv").read_text(), before)
        self.assertEqual(len(self.conv_log.read_text().splitlines()), 3)

    def test_free_refuses_when_jpg_missing(self) -> None:
        (self.folder / "keep.dpx").write_bytes(b"keep-dpx-bytes")
        (self.folder / "gone.dpx").write_bytes(b"gone-dpx-bytes")
        swept = self._run(["--sweep", "JOB", self.project])
        self.assertEqual(swept.returncode, 0, self._text(swept))
        rel = next(row[4] for row in self._manifest("JOB") if row[1] == "gone.dpx")
        jpg = self.share / "JOB" / rel
        jpg.unlink()
        jpg.with_name(jpg.stem + ".partial.jpg").write_bytes(b"leftover-partial")

        result = self._run(["--free", "JOB", self.project])
        self.assertEqual(result.returncode, 1, self._text(result))
        self.assertIn("gone.dpx", result.stdout)
        self.assertIn("1 DPX", result.stdout)
        self.assertNotIn("keep.dpx", result.stdout)
        self.assertNotIn("rm -rf", result.stdout)
        self.assertNotIn("rm -rf", result.stderr)
        self.assertNotIn("xargs", result.stdout)
        self.assertFalse((self.pg_tmp / "JOB_free_list.txt").exists())

    def test_free_refuses_on_md5_mismatch(self) -> None:
        (self.folder / "stable.dpx").write_bytes(b"stable-dpx-bytes-aaaa")
        changed = self.folder / "changed.dpx"
        changed.write_bytes(b"changed-dpx-bytes-bbbb")
        swept = self._run(["--sweep", "JOB", self.project])
        self.assertEqual(swept.returncode, 0, self._text(swept))
        raw = bytearray(changed.read_bytes())
        raw[0] ^= 0xFF
        changed.write_bytes(bytes(raw))

        result = self._run(["--free", "JOB", self.project])
        self.assertEqual(result.returncode, 1, self._text(result))
        self.assertIn("changed.dpx", result.stdout)
        self.assertNotIn("stable.dpx", result.stdout)
        self.assertNotIn("rm -rf", result.stdout)
        self.assertNotIn("rm -rf", result.stderr)
        self.assertNotIn("xargs", result.stdout)
        self.assertFalse((self.pg_tmp / "JOB_free_list.txt").exists())

    def test_free_lists_only_dpx_files(self) -> None:
        names = ["a.dpx", "b.dpx", "c.dpx"]
        for name in names:
            (self.folder / name).write_bytes(f"payload-{name}".encode())
        (self.folder / "Project.db.20261015192759").write_bytes(b"backup-db")
        (self.folder / "Project.db.20261016193156-journal").write_bytes(b"backup-journal")
        (self.folder / "other.bin").write_bytes(b"not-a-dpx")
        swept = self._run(["--sweep", "JOB", self.project])
        self.assertEqual(swept.returncode, 0, self._text(swept))

        result = self._run(["--free", "JOB", self.project])
        self.assertEqual(result.returncode, 0, self._text(result))
        listed = (self.pg_tmp / "JOB_free_list.txt").read_text().splitlines()
        self.assertEqual(listed, [str(self.folder / name) for name in names])
        blob = "\n".join(listed)
        self.assertNotIn("Project.db.20261015192759", blob)
        self.assertNotIn("Project.db.20261016193156-journal", blob)
        self.assertNotIn("other.bin", blob)
        self.assertNotIn("rm -rf", result.stdout)
        self.assertNotIn("rm -rf", result.stderr)
        self.assertIn(str(self.pg_tmp / "JOB_free_list.txt"), result.stdout)
        self.assertIn("xargs -0 rm --", result.stdout)
        self.assertIn("Project.db.* files are Resolve project backups and stay.", result.stdout)
        self.assertIn(f"{self.project}: 3 non-DPX files stay, 0.0 GiB", result.stdout)
        self.assertIn("This frees", result.stdout)
        self.assertIn("Your hands, not mine", result.stdout)

    def test_free_names_at_most_ten_missing(self) -> None:
        for i in range(12):
            (self.folder / f"f{i:02d}.dpx").write_bytes(f"dpx-{i:02d}".encode())
        swept = self._run(["--sweep", "JOB", self.project])
        self.assertEqual(swept.returncode, 0, self._text(swept))
        for jpg in (self.share / "JOB" / "_dpx").rglob("*.jpg"):
            if jpg.name.endswith(".partial.jpg"):
                continue
            jpg.unlink()
        result = self._run(["--free", "JOB", self.project])
        self.assertEqual(result.returncode, 1, self._text(result))
        self.assertIn("12 DPX", result.stdout)
        for i in range(10):
            self.assertIn(f"f{i:02d}.dpx", result.stdout)
        self.assertNotIn("f10.dpx", result.stdout)
        self.assertNotIn("f11.dpx", result.stdout)
        self.assertNotIn("rm -rf", result.stdout)
        self.assertNotIn("xargs", result.stdout)

    def test_verify_old_manifest_only(self) -> None:
        self._plant_album("OLD")
        result = self._run(["--verify", "OLD"])
        self.assertEqual(result.returncode, 0, self._text(result))
        self.assertIn("OLD: 1 jpg, 0 drx, 1 manifest lines,", result.stdout)
        self.assertIn("VERIFIED: every manifest line has a file with bytes in it.", result.stdout)

    def test_verify_dpx_manifest_jpg_must_have_bytes(self) -> None:
        self._plant_album("JOB")
        man = self.share / "JOB" / "_dpx_manifest.tsv"
        man.write_text("ALBUM TEST\tgone.dpx\t4\tabc\t_dpx/ALBUM_TEST/gone.jpg\t4\n")
        missing = self._run(["--verify", "JOB"])
        self.assertEqual(missing.returncode, 1, self._text(missing))
        self.assertIn("gone.dpx", missing.stdout)
        self.assertIn("not verified, do not free anything", missing.stdout)
        self.assertNotIn("VERIFIED:", missing.stdout)

        jpg = self.share / "JOB" / "_dpx" / "ALBUM_TEST" / "gone.jpg"
        jpg.parent.mkdir(parents=True)
        jpg.write_bytes(b"jpeg-bytes")
        present = self._run(["--verify", "JOB"])
        self.assertEqual(present.returncode, 0, self._text(present))
        self.assertIn("VERIFIED: every manifest line has a file with bytes in it.", present.stdout)

        partial = jpg.with_name("gone.partial.jpg")
        jpg.rename(partial)
        man.write_text("ALBUM TEST\tgone.dpx\t4\tabc\t_dpx/ALBUM_TEST/gone.partial.jpg\t4\n")
        leftover = self._run(["--verify", "JOB"])
        self.assertEqual(leftover.returncode, 1, self._text(leftover))
        self.assertNotIn("VERIFIED:", leftover.stdout)

    def test_free_refuses_when_album_export_is_not_verified(self) -> None:
        (self.folder / "a.dpx").write_bytes(b"dpx-bytes-aaa")
        swept = self._run(["--sweep", "JOB", self.project])
        self.assertEqual(swept.returncode, 0, self._text(swept))
        self._plant_album("JOB", lines=2)
        result = self._run(["--free", "JOB", self.project])
        self.assertEqual(result.returncode, 1, self._text(result))
        self.assertIn("Not printing a removal command: the share copy is not verified.", result.stdout)
        self.assertNotIn("rm -rf", result.stdout)
        self.assertNotIn("rm -rf", result.stderr)
        self.assertNotIn("xargs", result.stdout)
        self.assertFalse((self.pg_tmp / "JOB_free_list.txt").exists())

    def test_two_projects_are_both_swept_and_listed(self) -> None:
        uuid2 = "22222222-3333-4444-5555-666666666666"
        name2 = "SECOND PROJECT"
        self._add_project(uuid2, name2)
        folder2 = self.stills / uuid2
        folder2.mkdir()
        (self.folder / "a.dpx").write_bytes(b"aaa-dpx")
        (folder2 / "b.dpx").write_bytes(b"bbb-dpx")
        (folder2 / "Project.db.20261015192759").write_bytes(b"backup")

        swept = self._run(["--sweep", "JOB", self.project, name2])
        self.assertEqual(swept.returncode, 0, self._text(swept))
        self.assertIn(f"{self.project}: 1 converted, 0 skipped, 0 empty, 0 failed", swept.stdout)
        self.assertIn(f"{name2}: 1 converted, 0 skipped, 0 empty, 0 failed", swept.stdout)

        result = self._run(["--free", "JOB", self.project, name2])
        self.assertEqual(result.returncode, 0, self._text(result))
        listed = (self.pg_tmp / "JOB_free_list.txt").read_text().splitlines()
        self.assertEqual(listed, [str(self.folder / "a.dpx"), str(folder2 / "b.dpx")])
        self.assertNotIn("Project.db.20261015192759", "\n".join(listed))
        self.assertNotIn("rm -rf", result.stdout)

    def test_zero_byte_dpx_is_empty_not_failed(self) -> None:
        files = {
            "one.dpx": b"ONE-dpx-payload-0001",
            "two.dpx": b"TWO-dpx-payload-0002",
        }
        for name, data in files.items():
            (self.folder / name).write_bytes(data)
        (self.folder / "empty.dpx").write_bytes(b"")
        (self.folder / "Project.db.20261015192759").write_bytes(b"backup-db")
        (self.folder / "Project.db.20261016193156-journal").write_bytes(b"backup-journal")

        swept = self._run(["--sweep", "JOB", self.project])
        self.assertEqual(swept.returncode, 0, self._text(swept))
        self.assertIn(f"{self.project}: 2 converted, 0 skipped, 1 empty, 0 failed", swept.stdout)
        self.assertNotIn("  failed ", swept.stdout)

        rows = {row[1]: row for row in self._manifest("JOB")}
        self.assertEqual(set(rows), set(files))
        manifest = (self.share / "JOB" / "_dpx_manifest.tsv").read_text()
        self.assertNotIn("empty.dpx", manifest)
        self.assertFalse((self.share / "JOB" / "_dpx" / "ALBUM_TEST" / "empty.jpg").exists())
        logged = self.conv_log.read_text()
        self.assertEqual(len(logged.splitlines()), 2)
        self.assertNotIn("empty.dpx", logged)

        result = self._run(["--free", "JOB", self.project])
        self.assertEqual(result.returncode, 0, self._text(result))
        self.assertIn(
            "1 empty DPX files (0 bytes) are in the list: nothing in them to keep.",
            result.stdout,
        )
        listed = (self.pg_tmp / "JOB_free_list.txt").read_text().splitlines()
        self.assertEqual(
            listed,
            [str(self.folder / name) for name in ("empty.dpx", "one.dpx", "two.dpx")],
        )
        blob = "\n".join(listed)
        self.assertNotIn("Project.db.20261015192759", blob)
        self.assertNotIn("Project.db.20261016193156-journal", blob)
        self.assertNotIn("rm -rf", result.stdout)
        self.assertNotIn("rm -rf", result.stderr)
        self.assertIn("xargs -0 rm --", result.stdout)
        self.assertIn("Project.db.* files are Resolve project backups and stay.", result.stdout)

        (self.folder / "tiny.dpx").write_bytes(b"x")
        refused = self._run(["--free", "JOB", self.project])
        self.assertEqual(refused.returncode, 1, self._text(refused))
        self.assertIn("tiny.dpx", refused.stdout)
        self.assertIn("1 DPX files have no picture on the share", refused.stdout)
        self.assertNotIn("one.dpx", refused.stdout)
        self.assertNotIn("two.dpx", refused.stdout)
        self.assertNotIn("empty.dpx", refused.stdout)
        self.assertNotIn("nothing in them to keep", refused.stdout)
        self.assertNotIn("xargs", refused.stdout)
        self.assertNotIn("rm -rf", refused.stdout)
        self.assertNotIn("rm -rf", refused.stderr)
        self.assertEqual(
            (self.pg_tmp / "JOB_free_list.txt").read_text().splitlines(),
            listed,
        )

    @unittest.skipUnless(shutil.which("oiiotool"), "oiiotool is not on PATH")
    def test_sweep_with_oiiotool_then_free(self) -> None:
        a = self.folder / "a.dpx"
        b = self.folder / "b.dpx"
        for dest in (a, b):
            made = subprocess.run(
                ["oiiotool", "--pattern", "constant:color=0.2,0.4,0.6",
                 "2400x1350", "3", "-d", "uint10", "-o", str(dest)],
                capture_output=True, text=True,
            )
            self.assertEqual(made.returncode, 0, made.stdout + made.stderr)
            self.assertGreater(dest.stat().st_size, 0)
        (self.folder / "Project.db.20261015192759").write_bytes(b"backup-db")
        (self.folder / "Project.db.20261016193156-journal").write_bytes(b"backup-journal")
        (self.folder / "other.bin").write_bytes(b"not-a-dpx")

        swept = self._run(["--sweep", "JOB", self.project], convert=False, timeout=180)
        self.assertEqual(swept.returncode, 0, self._text(swept))
        self.assertIn("2 converted, 0 skipped, 0 empty, 0 failed", swept.stdout)
        for stem in ("a", "b"):
            jpg = self.share / "JOB" / "_dpx" / "ALBUM_TEST" / f"{stem}.jpg"
            self.assertTrue(jpg.is_file() and jpg.stat().st_size > 0, jpg)
            self.assertEqual(_jpeg_width(jpg), 1920, jpg)
        self.assertEqual(list((self.share / "JOB").rglob("*partial*")), [])

        result = self._run(["--free", "JOB", self.project], convert=False)
        self.assertEqual(result.returncode, 0, self._text(result))
        listed = (self.pg_tmp / "JOB_free_list.txt").read_text().splitlines()
        self.assertEqual(listed, [str(a), str(b)])
        self.assertNotIn("rm -rf", result.stdout)
        self.assertNotIn("rm -rf", result.stderr)


def _jpeg_width(path: Path) -> int:
    if shutil.which("sips"):
        info = subprocess.run(
            ["sips", "-g", "pixelWidth", str(path)],
            capture_output=True, text=True,
        )
        for line in (info.stdout or "").splitlines():
            if "pixelWidth" in line and ":" in line:
                return int(line.split(":")[-1].strip())
    info = subprocess.run(
        ["oiiotool", "--info", str(path)],
        capture_output=True, text=True,
    )
    match = re.search(r"(\d+)\s*x\s*\d+", (info.stdout or "") + (info.stderr or ""))
    if not match:
        raise AssertionError((info.stdout or "") + (info.stderr or ""))
    return int(match.group(1))


if __name__ == "__main__":
    unittest.main()
