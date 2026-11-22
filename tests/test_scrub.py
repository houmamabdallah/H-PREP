"""Check the tracked public tree, including the checker's own source."""
import hashlib
import json
import os
from pathlib import Path
import py_compile
import re
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT)


class ScrubTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.files = [Path(os.fsdecode(p)) for p in git("ls-files", "-z").split(b"\0") if p]

    def scan(self, pattern, license_exception=False):
        # Decode every file, including binary data, without losing ASCII matches.
        for path in self.files:
            lines = (ROOT / path).read_bytes().decode("utf-8", errors="surrogateescape").splitlines()
            for number, line in enumerate([path.as_posix(), *lines]):
                if license_exception and path == Path("LICENSE") and line == "Copyright (c) 2026 Houmam Abdallah":
                    continue
                if pattern.search(line):
                    self.fail(f"Tracked file #{self.files.index(path) + 1}, line {number} matches a restricted term")

    def test_machine_paths(self):
        with self.subTest(arm="machine_paths"):
            # Split blocked literals so the rules also pass their own scan.
            words = ("/" + "Users/", "/" + "Volumes/", "HA" + "SE", "POWER" + "GRADES",
                     "houmam" + "abdallah")
            patterns = [re.escape(word) for word in words] + ["(?i:look" + "bank)"]
            self.scan(re.compile("|".join(patterns)), license_exception=True)

    def test_private_names(self):
        with self.subTest(arm="private_names"):
            default = Path.home() / ".config/h-prep/denylist.txt"
            path = Path(os.environ.get("HPREP_DENYLIST") or default).expanduser()
            if not path.is_file():
                self.fail(f"Put the private denylist at {path} or set HPREP_DENYLIST to its location")
            names = [n.strip() for n in path.read_text(encoding="utf-8").splitlines() if n.strip()]
            self.assertTrue(names, "The denylist needs at least one entry")
            self.scan(re.compile("|".join(map(re.escape, names)), re.I))

    def test_private_dependencies(self):
        with self.subTest(arm="private_dependencies"):
            terms = ("engi" + "nes", "look" + "bank", "hu" + "e_", "matte_" + "tool",
                     "people_" + "reel", "run_" + "job.sh", "matte" + r"[ _-]*generat",
                     r"generat\w*[ _-]+" + "mattes?", "make" + r"[ _-]+mattes?")
            self.scan(re.compile("|".join(terms), re.I))

    def test_config_keys(self):
        with self.subTest(arm="config_keys"):
            example = json.loads((ROOT / "config.example.json").read_text())
            self.assertIsInstance(example, dict)
            keys = set()
            for path in self.files:
                if path.suffix in (".py", ".sh"):
                    source = (ROOT / path).read_text()
                    keys.update(re.findall(r"""\b(?:cfg|setting_path)\(\s*['"]([\w]+)['"]""", source))
                    keys.update(re.findall(r"\$\(path_setting ([a-z_]+)\)", source))
            self.assertTrue(keys)
            self.assertEqual(set(), keys - example.keys(), "Add missing settings to the example")

    def test_local_config(self):
        with self.subTest(arm="local_config"):
            self.assertNotIn(Path("config.json"), self.files)
            ignore = (ROOT / ".gitignore").read_text().splitlines()
            self.assertTrue({"config.json", "/config.json"}.intersection(ignore))
            result = subprocess.run(["git", "check-ignore", "--no-index", "-q", "config.json"], cwd=ROOT)
            self.assertEqual(result.returncode, 0, "config.json needs an active ignore rule")

    def test_syntax(self):
        with self.subTest(arm="syntax"), tempfile.TemporaryDirectory() as tmp:
            for index, path in enumerate(self.files):
                if path.suffix == ".py":
                    py_compile.compile(str(ROOT / path), cfile=str(Path(tmp) / str(index)), doraise=True)
                elif path.suffix == ".sh":
                    subprocess.run(["bash", "-n", str(ROOT / path)], check=True)

    def test_gallery_sweep(self):
        with self.subTest(arm="gallery_sweep"):
            suite = ROOT / "tools/gallery_archive/test_pg_move_sweep.py"
            result = subprocess.run([sys.executable, str(suite)], cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_mit_license(self):
        with self.subTest(arm="mit_license"):
            title, copyright_line, terms = (ROOT / "LICENSE").read_text().strip().split("\n\n", 2)
            self.assertEqual(title, "MIT License")
            self.assertEqual(copyright_line, "Copyright (c) 2026 Houmam Abdallah")
            digest = hashlib.sha256(" ".join(terms.split()).encode()).hexdigest()
            self.assertEqual(digest, "fe2a9817987f862eaced948f0468c7f51d2fedfc48c5c505b246a49a3870e9a5", "MIT license text changed")
