"""Public site composition excludes private evidence and survives archival."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
import contextlib
import io
import json

from toki_finance_daily.cli import archive_outputs, main

ROOT = Path(__file__).resolve().parents[1]


class SiteCLITests(TestCase):
    def test_standalone_site_publishes_reader_artifacts_without_private_inputs(self):
        dataset = json.loads((ROOT / "examples/dataset.json").read_text())
        brief = json.loads((ROOT / "examples/brief.json").read_text())
        with TemporaryDirectory() as directory:
            private = Path(directory).resolve()
            public = private / "site"
            (private / "dataset.json").write_text(json.dumps(dataset, ensure_ascii=False))
            (private / "brief.json").write_text(json.dumps(brief, ensure_ascii=False))
            (private / "poster.png").write_bytes(b"reader-image")
            (private / "poster.html").write_text("reader-poster")
            (private / "poster-mobile.png").write_bytes(b"reader-mobile-image")
            (private / "raw").mkdir()
            (private / "raw" / "private.bin").write_bytes(b"raw-private-source")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(["site", "--dataset", str(private / "dataset.json"),
                             "--brief", str(private / "brief.json"), "--out", str(public)])
            self.assertEqual(code, 0)
            paths = json.loads(output.getvalue())
            self.assertEqual(Path(paths["html"]), public / "index.html")
            self.assertTrue((public / "public-data.json").is_file())
            self.assertTrue((public / "technical.html").is_file())
            self.assertTrue((public / "technicals.csv").is_file())
            self.assertEqual((public / "poster-mobile.png").read_bytes(), b"reader-mobile-image")
            for name in ("dataset.json", "brief.json", "manifest.json", "raw"):
                self.assertFalse((public / name).exists(), name)
            self.assertTrue((private / "dataset.json").is_file())

    def test_archive_preserves_public_site_and_rewrites_nested_and_mobile_paths(self):
        with TemporaryDirectory() as directory:
            folder = Path(directory).resolve()
            (folder / "site").mkdir()
            (folder / "site" / "index.html").write_text("previous-site")
            (folder / "site" / "public-data.json").write_text("previous-public-data")
            (folder / "poster.html").write_text("previous-poster")
            (folder / "poster-mobile.png").write_bytes(b"previous-mobile")
            original = {"status": "ready", "html": str(folder / "poster.html"),
                        "mobile_png": str(folder / "poster-mobile.png"),
                        "site_html": str(folder / "site" / "index.html"),
                        "public_data": str(folder / "site" / "public-data.json"),
                        "mobile_layout": {"html_path": str(folder / "poster.html"),
                                          "png_path": str(folder / "poster-mobile.png")}}
            (folder / "manifest.json").write_text(json.dumps(original))
            archive_outputs(folder)
            archived = next((folder / "history").iterdir())
            previous = json.loads((archived / "manifest.json").read_text())
            self.assertEqual(Path(previous["site_html"]), archived / "site" / "index.html")
            self.assertEqual(Path(previous["public_data"]), archived / "site" / "public-data.json")
            self.assertEqual(Path(previous["mobile_layout"]["png_path"]), archived / "poster-mobile.png")
            self.assertEqual((archived / "site" / "index.html").read_text(), "previous-site")
            self.assertFalse((folder / "site").exists())
