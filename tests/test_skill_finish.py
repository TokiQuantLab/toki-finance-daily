"""The Skill's deterministic handoff stages reader files before publishing."""
from contextlib import ExitStack
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import sys
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from toki_finance_daily.brief import BriefError
from toki_finance_daily.render import render_html

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills/toki-finance-daily/scripts/finish.py"
spec = importlib.util.spec_from_file_location("skill_finish", SCRIPT)
finish = importlib.util.module_from_spec(spec)
spec.loader.exec_module(finish)


def png_stub(path, width, height):
    # Header-only stand-ins: these tests verify composition and dimensions;
    # actual Chromium rendering is checked separately, once per delivery.
    Path(path).write_bytes(b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", width, height))


def fake_png(html_path, png_path, width=1080, scale=None):
    scale = scale or 1
    height = 3200 if width == 1080 else 5400
    png_stub(png_path, round(width * scale), round(height * scale))
    return {"ok": True, "width": width, "height": height, "device_scale_factor": scale,
            "pixel_width": round(width * scale), "pixel_height": round(height * scale),
            "html_path": str(html_path), "png_path": str(png_path),
            "horizontal_overflow": [], "text_overflow": [], "document_horizontal_overflow": False}


def fake_cover(html_path, png_path):
    png_stub(png_path, 1080, 1440)


class SkillFinishTests(TestCase):
    def inputs(self, folder):
        folder.mkdir(parents=True, exist_ok=True)
        # Preserve intentionally different whitespace to catch normalization.
        for name in ("dataset.json", "brief.json"):
            value = json.loads((ROOT / "examples" / name).read_text())
            (folder / name).write_text(json.dumps(value, ensure_ascii=False, indent=1) + "\n\n", encoding="utf-8")
        return folder / "dataset.json", folder / "brief.json"

    def mocks(self, stack):
        stack.enter_context(patch("toki_finance_daily.render.render_png", side_effect=fake_png))
        stack.enter_context(patch.object(finish, "render_png", side_effect=fake_png))
        stack.enter_context(patch.object(finish, "_render_cover", side_effect=fake_cover))

    def old_delivery(self, private, public):
        private.mkdir(parents=True)
        public.mkdir(parents=True)
        (private / "poster.html").write_text("previous poster")
        (private / "raw").mkdir()
        (private / "raw" / "evidence.bin").write_bytes(b"private evidence")
        (private / "diagnostics.json").write_text("private diagnostics")
        (private / "manifest.json").write_text(json.dumps({"html": str(private / "poster.html")}))
        for name in finish.READER_FILES:
            (public / name).write_text("previous " + name)

    def test_success_uses_original_templates_and_preserves_inputs_with_eight_public_files(self):
        with TemporaryDirectory() as directory, ExitStack() as stack:
            base = Path(directory).resolve()
            dataset_path, brief_path = self.inputs(base / "input")
            original = {path: path.read_bytes() for path in (dataset_path, brief_path)}
            templates = [ROOT / "toki_finance_daily" / "render.py", *sorted((ROOT / "toki_finance_daily/assets").glob("*"))]
            hashes = {path: sha256(path.read_bytes()).hexdigest() for path in templates if path.is_file()}
            private, public = base / "private", base / "public"
            self.old_delivery(private, public)
            self.mocks(stack)
            copied_public = []
            atomic_copy = finish._atomic_copy
            def track(source, target):
                if target.parent == public:
                    copied_public.append(target.name)
                    if target.name != "index.html":
                        self.assertEqual((public / "index.html").read_text(), "previous index.html")
                atomic_copy(source, target)
            stack.enter_context(patch.object(finish, "_atomic_copy", side_effect=track))
            manifest = finish.finish(dataset_path, brief_path, private, public)

            self.assertEqual(copied_public, list(finish.READER_FILES))
            self.assertEqual(copied_public[-1], "index.html")
            self.assertEqual({path.name for path in public.iterdir()}, set(finish.READER_FILES))
            self.assertEqual({path.name for path in (private / "site").iterdir()}, set(finish.READER_FILES))
            self.assertEqual(finish._png_size(private / "poster.png"), (1080, 3200))
            self.assertEqual(finish._png_size(private / "poster-mobile.png"), (1080, 10800))
            self.assertEqual(finish._png_size(private / "poster-cover.png"), (1080, 1440))
            expected = render_html(json.loads(original[brief_path]), json.loads(original[dataset_path])).encode()
            self.assertEqual((private / "poster.html").read_bytes(), expected)
            for path, content in original.items():
                self.assertEqual(path.read_bytes(), content)
                self.assertEqual((private / path.name).read_bytes(), content)
            self.assertEqual({path: sha256(path.read_bytes()).hexdigest() for path in hashes}, hashes)
            self.assertEqual((private / "raw/evidence.bin").read_bytes(), b"private evidence")
            self.assertEqual((private / "diagnostics.json").read_text(), "private diagnostics")
            previous = next((private / "history").iterdir())
            self.assertEqual((previous / "poster.html").read_text(), "previous poster")
            self.assertEqual(json.loads((previous / "manifest.json").read_text())["html"], str(previous / "poster.html"))

            for key, name in (("html", "poster.html"), ("png", "poster.png"), ("mobile_png", "poster-mobile.png"),
                              ("cover_png", "poster-cover.png"), ("technical_html", "technical.html"), ("technical_csv", "technicals.csv")):
                self.assertEqual(manifest[key], str(private / name))
            self.assertEqual(manifest["site_html"], str(public / "index.html"))
            self.assertEqual(manifest["public_data"], str(public / "public-data.json"))
            self.assertEqual(manifest["layout"]["html_path"], manifest["html"])
            self.assertEqual(manifest["layout"]["png_path"], manifest["png"])
            self.assertEqual(manifest["mobile_layout"]["png_path"], manifest["mobile_png"])
            self.assertNotIn("toki-finish-", json.dumps(manifest))
            self.assertEqual(json.loads((private / "manifest.json").read_text()), manifest)
            public_data = json.loads((public / "public-data.json").read_text())
            self.assertTrue(public_data["assets"]["online"])
            for asset in public_data["assets"]["downloads"]:
                self.assertTrue((public / asset["path"]).is_file())
            self.assertEqual((public / "technicals.csv").read_bytes(), (private / "technicals.csv").read_bytes())

    def test_private_input_files_and_nested_site_are_supported_without_rewriting_inputs(self):
        with TemporaryDirectory() as directory, ExitStack() as stack:
            private = Path(directory).resolve() / "report"
            dataset_path, brief_path = self.inputs(private)
            original = [dataset_path.read_bytes(), brief_path.read_bytes()]
            (private / "poster.html").write_text("old")
            self.mocks(stack)
            manifest = finish.finish(dataset_path, brief_path, private, private / "site")
            self.assertEqual([dataset_path.read_bytes(), brief_path.read_bytes()], original)
            self.assertEqual(manifest["site_html"], str(private / "site/index.html"))
            historical = next((private / "history").iterdir())
            self.assertEqual((historical / "dataset.json").read_bytes(), original[0])
            self.assertEqual({p.name for p in (private / "site").iterdir()}, set(finish.READER_FILES))

    def test_staging_failures_leave_old_live_and_private_outputs_untouched(self):
        for failure in ("mobile", "cover", "public_files", "brief"):
            with self.subTest(failure=failure), TemporaryDirectory() as directory, ExitStack() as stack:
                base = Path(directory).resolve()
                dataset_path, brief_path = self.inputs(base / "input")
                private, public = base / "private", base / "public"
                self.old_delivery(private, public)
                self.mocks(stack)
                if failure == "mobile":
                    stack.enter_context(patch.object(finish, "render_png", return_value={"ok": False, "width": 540}))
                elif failure == "cover":
                    stack.enter_context(patch.object(finish, "_render_cover", side_effect=RuntimeError("cover failed")))
                elif failure == "public_files":
                    original_site = finish.cli.site
                    def extra_file(*args, **kwargs):
                        result = original_site(*args, **kwargs)
                        (Path(args[2]) / "dataset.json").write_text("must never publish")
                        return result
                    stack.enter_context(patch.object(finish.cli, "site", side_effect=extra_file))
                else:
                    brief = json.loads(brief_path.read_text())
                    brief["lead"]["evidence_ids"] = ["missing"]
                    brief_path.write_text(json.dumps(brief))
                before = {p: p.read_bytes() for folder in (private, public) for p in folder.rglob("*") if p.is_file()}
                with self.assertRaises((BriefError, RuntimeError)):
                    finish.finish(dataset_path, brief_path, private, public)
                self.assertEqual({p: p.read_bytes() for folder in (private, public) for p in folder.rglob("*") if p.is_file()}, before)
                self.assertFalse((private / "history").exists())

    def test_private_directory_conflicts_are_rejected_before_rendering(self):
        with TemporaryDirectory() as directory, patch.object(finish.cli, "render") as render:
            base = Path(directory).resolve()
            dataset_path, brief_path = self.inputs(base / "input")
            private = base / "private"
            for public in (private, base, private / "raw/site", private / "history/site", private / "diagnostics/site"):
                with self.subTest(public=public), self.assertRaises(BriefError):
                    finish.finish(dataset_path, brief_path, private, public)
            private.mkdir()
            symlink = base / "public-symlink"
            symlink.symlink_to(private, target_is_directory=True)
            with self.assertRaises(BriefError):
                finish.finish(dataset_path, brief_path, private, symlink)
            public_input, public_brief = self.inputs(private / "site")
            with self.assertRaises(BriefError):
                finish.finish(public_input, public_brief, private, base / "public")
            render.assert_not_called()

    def test_duplicate_records_fail_validation_before_rendering(self):
        with TemporaryDirectory() as directory, patch.object(finish.cli, "render") as render:
            base = Path(directory).resolve()
            dataset_path, brief_path = self.inputs(base / "input")
            dataset = json.loads(dataset_path.read_text())
            dataset["records"].append(dataset["records"][0])
            dataset_path.write_text(json.dumps(dataset))
            with self.assertRaisesRegex(BriefError, "重复"):
                finish.finish(dataset_path, brief_path, base / "private", base / "public")
            render.assert_not_called()

    def test_wrapper_help_works_from_arbitrary_cwd_and_preserves_old_cli(self):
        with TemporaryDirectory() as directory:
            for args, expected in ((["--help"], "collect"), (["finish", "--help"], "--site-out")):
                process = subprocess.run([sys.executable, str(SCRIPT.with_name("daily.py")), *args], cwd=directory,
                                         text=True, capture_output=True)
                self.assertEqual(process.returncode, 0, process.stderr)
                self.assertIn(expected, process.stdout)
                if args[0] == "finish":
                    self.assertIn("Agent 先写 Brief", process.stdout)
                    self.assertIn("不调用 AI 成稿或采集", " ".join(process.stdout.split()))
