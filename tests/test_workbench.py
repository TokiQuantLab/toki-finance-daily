"""Online companion output remains separate from frozen poster evidence."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
import contextlib
import io
import json

from toki_finance_daily.cli import archive_outputs, main, render

ROOT = Path(__file__).resolve().parents[1]


def fake_workbench(folder):
    path = Path(folder) / "technical.html"
    path.write_text('<html><script src="https://example.org/live-widget.js"></script></html>', encoding="utf-8")
    return str(path.resolve())


class WorkbenchTests(TestCase):
    def test_render_opt_in_has_separate_manifest_and_unchanged_poster_evidence(self):
        dataset = json.loads((ROOT / "examples/dataset.json").read_text())
        brief = json.loads((ROOT / "examples/brief.json").read_text())
        original_dataset, original_brief = deepcopy(dataset), deepcopy(brief)

        def screenshot(html_path, png_path, **options):
            html = Path(html_path).read_text()
            self.assertIn("script-src 'none'", html)
            self.assertNotIn("live-widget.js", html)
            Path(png_path).write_bytes(b"frozen-poster-image")
            return {"ok": True, "html_path": str(Path(html_path).resolve()), "png_path": str(Path(png_path).resolve())}

        for enabled in (False, True):
            with self.subTest(enabled=enabled), TemporaryDirectory() as tmp, \
                 patch("toki_finance_daily.cli.workbench", side_effect=fake_workbench) as companion, \
                 patch("toki_finance_daily.render.render_png", side_effect=screenshot) as png, \
                 contextlib.redirect_stdout(io.StringIO()):
                folder = Path(tmp)
                manifest = render(dataset, brief, folder, width=540, scale=2, with_workbench=enabled)
                self.assertEqual(Path(manifest["html"]), (folder / "poster.html").resolve())
                self.assertEqual(Path(manifest["png"]), (folder / "poster.png").resolve())
                self.assertEqual((folder / "poster.png").read_bytes(), b"frozen-poster-image")
                self.assertEqual(png.call_args.kwargs, {"width": 540, "scale": 2})
                self.assertEqual(json.loads((folder / "dataset.json").read_text()), original_dataset)
                self.assertEqual(json.loads((folder / "brief.json").read_text()), original_brief)
                self.assertEqual(json.loads((folder / "manifest.json").read_text()), manifest)
                if enabled:
                    companion.assert_called_once_with(folder)
                    self.assertEqual(Path(manifest["technical_html"]), (folder / "technical.html").resolve())
                    self.assertNotEqual(manifest["technical_html"], manifest["html"])
                    self.assertIn("live-widget.js", (folder / "technical.html").read_text())
                else:
                    companion.assert_not_called()
                    self.assertNotIn("technical_html", manifest)
                    self.assertFalse((folder / "technical.html").exists())
        self.assertEqual(dataset, original_dataset)
        self.assertEqual(brief, original_brief)

    def test_companion_only_previous_output_archives_and_rewrites_its_manifest_path(self):
        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            companion = folder / "technical.html"
            companion.write_text("previous-online-companion")
            dataset = {"mode": "demo", "records": [{"id": "frozen-evidence"}]}
            (folder / "dataset.json").write_text(json.dumps(dataset))
            (folder / "brief.json").write_text(json.dumps({"headline": "previous"}))
            (folder / "manifest.json").write_text(json.dumps({"status": "ready", "html": None, "png": None,
                                                              "technical_html": str(companion.resolve())}))
            archive_outputs(folder)
            archived = list((folder / "history").glob("*/technical.html"))
            self.assertEqual(len(archived), 1)
            self.assertEqual(archived[0].read_text(), "previous-online-companion")
            self.assertFalse(companion.exists())
            previous = json.loads(archived[0].with_name("manifest.json").read_text())
            self.assertEqual(previous["technical_html"], str(archived[0].resolve()))
            self.assertEqual(json.loads(archived[0].with_name("dataset.json").read_text()), dataset)
            self.assertEqual(json.loads((folder / "dataset.json").read_text()), dataset)

    def test_standalone_command_leaves_existing_poster_dataset_and_manifest_intact(self):
        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            previous = {"poster.html": b"previous-offline-html", "poster.png": b"previous-pixels",
                        "dataset.json": b'{"records":[]}', "manifest.json": b'{"status":"ready","png":"existing"}'}
            for name, body in previous.items():
                (folder / name).write_bytes(body)
            output = io.StringIO()
            with patch("toki_finance_daily.cli.workbench", side_effect=fake_workbench), \
                 patch("toki_finance_daily.collect.collect") as collect, \
                 patch("toki_finance_daily.render.render_png") as png, \
                 contextlib.redirect_stdout(output):
                code = main(["workbench", "--out", tmp])
            self.assertEqual(code, 0)
            collect.assert_not_called()
            png.assert_not_called()
            result = json.loads(output.getvalue())
            self.assertTrue(result["requires_network"])
            self.assertEqual(result["technical_html"], str((folder / "technical.html").resolve()))
            for name, body in previous.items():
                self.assertEqual((folder / name).read_bytes(), body)
