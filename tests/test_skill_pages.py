"""Only frozen reader files can reach the Pages connector handoff."""
import base64
from contextlib import redirect_stderr, redirect_stdout
from hashlib import sha256
from html import unescape
import importlib.util
import io
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit


SCRIPT = Path(__file__).resolve().parents[1] / "skills/toki-finance-daily/scripts/pages.py"
spec = importlib.util.spec_from_file_location("skill_pages", SCRIPT)
pages = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pages)


class SkillPagesTests(TestCase):
    def inputs(self, root, *, mode="live", edition_date="2026-10-11"):
        site = root / "site"
        site.mkdir()
        public = {
            "schema_version": 1, "date": edition_date, "mode": mode,
            "timezone": "Asia/Shanghai", "cutoff": edition_date + "T22:00:00+08:00",
            "brief": {"headline": "测试日报"}, "records": [],
        }
        for name in pages.READER_FILES:
            (site / name).write_bytes(b"reader file " + name.encode() + bytes(range(256)))
        (site / "public-data.json").write_text(json.dumps(public), encoding="utf-8")
        embedded = json.dumps(public).replace("<", "\\u003c")
        (site / "index.html").write_text(
            '<html><head><title>原标题</title></head><body><script type="application/json" id="daily-data">'
            + embedded + '</script></body></html>',
            encoding="utf-8",
        )
        return site, root / "staging"

    def prepare(self, site, out, **kwargs):
        return pages.prepare(site, out, "2026-10-11", "https://example.com/daily/", **kwargs)

    def test_snapshot_contains_only_reader_files_and_survives_source_changes(self):
        with TemporaryDirectory() as directory:
            site, out = self.inputs(Path(directory))
            (site / "unrelated.txt").write_text("must not be published")
            manifest = self.prepare(site, out)
            self.assertEqual({p.name for p in (out / "files").iterdir()}, set(pages.PUBLISH_FILES))
            self.assertEqual(json.loads((out / "manifest.json").read_text()), manifest)
            self.assertEqual(manifest["date"], "2026-10-11")
            self.assertEqual(manifest["mode"], "live")
            self.assertEqual(manifest["url"], "https://example.com/daily/")
            self.assertEqual(manifest["chunk_size"], 65535)
            original = (site / "poster.png").read_bytes()
            (site / "poster.png").write_bytes(b"next edition")
            self.assertEqual(base64.b64decode(pages.blob(out / "manifest.json", "poster.png")), original)
            nojekyll = next(item for item in manifest["files"] if item["name"] == ".nojekyll")
            self.assertEqual((nojekyll["size"], nojekyll["chunks"]), (0, 0))

    def test_missing_empty_symlink_and_private_input_are_rejected(self):
        for problem in ("missing", "empty", "symlink", "private", "nested-staging"):
            with self.subTest(problem=problem), TemporaryDirectory() as directory:
                root = Path(directory)
                site, out = self.inputs(root)
                if problem == "missing":
                    (site / "poster.png").unlink()
                elif problem == "empty":
                    (site / "poster.png").write_bytes(b"")
                elif problem == "symlink":
                    (root / "image").write_bytes(b"private image")
                    (site / "poster.png").unlink()
                    (site / "poster.png").symlink_to(root / "image")
                elif problem == "private":
                    (site / "dataset.json").write_text("{}")
                else:
                    out = site / "staging"
                with self.assertRaises(pages.PagesError):
                    self.prepare(site, out)
                self.assertFalse(out.exists())

    def test_demo_stale_and_different_embedded_data_are_rejected(self):
        for problem in ("demo", "stale", "embedded"):
            with self.subTest(problem=problem), TemporaryDirectory() as directory:
                site, out = self.inputs(
                    Path(directory), mode="demo" if problem == "demo" else "live",
                    edition_date="2026-10-10" if problem == "stale" else "2026-10-11",
                )
                if problem == "embedded":
                    p = site / "index.html"
                    p.write_text(p.read_text().replace("测试日报", "different"))
                    # json.dumps escapes Chinese, so alter an ASCII field as well.
                    p.write_text(p.read_text().replace("22:00:00", "21:00:00"))
                with self.assertRaises(pages.PagesError):
                    self.prepare(site, out)
                self.assertFalse(out.exists())

    def test_base64_chunks_reassemble_original_bytes_and_stdout_has_no_newline(self):
        with TemporaryDirectory() as directory:
            site, out = self.inputs(Path(directory))
            original = bytes(range(256)) * 600
            (site / "poster.png").write_bytes(original)
            manifest = self.prepare(site, out)
            entry = next(item for item in manifest["files"] if item["name"] == "poster.png")
            self.assertEqual(entry["chunks"], 3)
            chunks = [pages.blob(out / "manifest.json", "poster.png", offset)
                      for offset in range(0, len(original), manifest["chunk_size"])]
            self.assertEqual(base64.b64decode("".join(chunks)), original)
            output = io.StringIO()
            with redirect_stdout(output):
                result = pages.main(["blob", "--manifest", str(out / "manifest.json"),
                                     "--name", "poster.png", "--offset", "3", "--size", "7"])
            self.assertEqual(result, 0)
            self.assertEqual(output.getvalue(), base64.b64encode(original[3:10]).decode())

    def test_unknown_name_changed_file_and_redirected_manifest_path_are_rejected(self):
        for problem in ("name", "changed", "redirect", "snapshot-symlink"):
            with self.subTest(problem=problem), TemporaryDirectory() as directory:
                site, out = self.inputs(Path(directory))
                self.prepare(site, out)
                name = "poster.png"
                if problem == "name":
                    name = "../../dataset.json"
                elif problem == "changed":
                    p = out / "files" / name
                    p.write_bytes(b"x" * p.stat().st_size)
                elif problem == "redirect":
                    p = out / "manifest.json"
                    manifest = json.loads(p.read_text())
                    next(item for item in manifest["files"] if item["name"] == name)["path"] = str(site / name)
                    p.write_text(json.dumps(manifest))
                else:
                    p = out / "files" / name
                    p.unlink()
                    p.symlink_to(site / name)
                with self.assertRaises(pages.PagesError):
                    pages.blob(out / "manifest.json", name)

    def test_invalid_cli_outputs_an_error_without_blob_payload(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            result = pages.main(["blob", "--manifest", "unused.json", "--name", "dataset.json"])
        self.assertEqual(result, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("白名单", stderr.getvalue())

    def test_public_metadata_and_fallback_are_safe_and_match_the_frozen_edition(self):
        with TemporaryDirectory() as directory:
            site, out = self.inputs(Path(directory))
            public = json.loads((site / "public-data.json").read_text())
            title = '日报 <>&" </script><script>bad()</script>'
            dek = '摘要 " & <img src=x onerror=bad()>'
            public["brief"].update(
                headline=title, dek=dek,
                lead={"title": "主线", "fact": "事实 <b>原样</b>",
                      "analysis": "分析 & 传导", "watch": "观察 <script>bad()</script>"},
                stories=[{"title": "其他 <事件>", "fact": "事实 & 依据"}],
            )
            (site / "public-data.json").write_text(json.dumps(public), encoding="utf-8")
            embedded = json.dumps(public).replace("<", "\\u003c").replace(">", "\\u003e")
            (site / "index.html").write_text(
                '<html><head><title>原标题</title></head><body>'
                '<noscript>旧摘要</noscript><script type="application/json" id="daily-data">'
                + embedded + '</script></body></html>', encoding="utf-8",
            )
            manifest = self.prepare(site, out)
            html = (out / "files/index.html").read_text()
            self.assertIn('<title>原标题</title>', html)
            description = re.search(r'<meta name="description" content="([^"]*)">', html).group(1)
            self.assertEqual(unescape(description), dek)
            self.assertIn('rel="canonical" href="https://example.com/daily/"', html)
            self.assertIn('property="og:image" content="https://example.com/daily/poster-cover.png"', html)
            self.assertIn('name="twitter:card" content="summary_large_image"', html)
            payload = re.search(r'<script type="application/ld\+json" id="daily-article">(.*?)</script>', html).group(1)
            self.assertNotIn('</script', payload.lower())
            article = json.loads(payload)
            self.assertEqual(article["headline"], title)
            self.assertEqual(article["description"], dek)
            self.assertNotIn("datePublished", article)
            self.assertNotIn("dateModified", article)
            self.assertEqual(article["author"]["name"], "Toki")
            self.assertEqual(article["publisher"]["name"], "TokiQuantLab")
            self.assertEqual(article["mainEntityOfPage"], manifest["url"])
            fallback = re.search(r'<noscript>(.*?)</noscript>', html, re.DOTALL).group(1)
            self.assertNotIn('<script>', fallback)
            self.assertNotIn('<img ', fallback)
            self.assertIn('事实 &lt;b&gt;原样&lt;/b&gt;', fallback)
            self.assertIn('其他 &lt;事件&gt;', fallback)
            reader = pages._EmbeddedData()
            reader.feed(html)
            self.assertEqual(json.loads(reader.entries[0]), public)
            entry = next(item for item in manifest["files"] if item["name"] == "index.html")
            self.assertEqual(entry["sha256"], sha256(html.encode("utf-8")).hexdigest())
            self.assertEqual(entry["size"], len(html.encode("utf-8")))
            self.assertNotIn('daily-article', (site / "index.html").read_text())

    def remote(self, out, *, missing=None, altered_index=False, on_request=None):
        contents = {name: (out / "files" / name).read_bytes() for name in pages.READER_FILES}
        if altered_index:
            contents["index.html"] = contents["index.html"].replace(b"2026-10-11", b"2026-10-10")

        def request(req, timeout):
            self.assertEqual(timeout, 20)
            self.assertFalse(req.has_header("Authorization"))
            name = Path(urlsplit(req.full_url).path).name
            if on_request:
                on_request(name)
            if name == missing:
                raise HTTPError(req.full_url, 404, "missing", None, None)
            response = io.BytesIO(contents[name])
            response.geturl = lambda: req.full_url
            return response
        return request

    def test_verify_rejects_old_html_even_if_public_json_is_new(self):
        with TemporaryDirectory() as directory:
            site, out = self.inputs(Path(directory))
            self.prepare(site, out)
            with patch.object(pages, "urlopen", side_effect=self.remote(out, altered_index=True)) as http:
                with self.assertRaisesRegex(pages.PagesError, "HTML.*JSON"):
                    pages.verify(out / "manifest.json")
            self.assertEqual(http.call_count, 2)

    def test_verify_confirms_all_eight_files_without_requesting_nojekyll(self):
        with TemporaryDirectory() as directory:
            site, out = self.inputs(Path(directory))
            self.prepare(site, out)
            with patch.object(pages, "urlopen", side_effect=self.remote(out)) as http:
                result = pages.verify(out / "manifest.json")
            self.assertEqual(result["status"], "verified")
            self.assertEqual(result["files"], 8)
            self.assertEqual(result["attempts"], 1)
            self.assertEqual(http.call_count, 8)
            self.assertFalse(any(".nojekyll" in call.args[0].full_url for call in http.call_args_list))

    def test_verify_rejects_missing_image_after_the_data_updates(self):
        with TemporaryDirectory() as directory:
            site, out = self.inputs(Path(directory))
            self.prepare(site, out)
            with patch.object(pages, "urlopen", side_effect=self.remote(out, missing="poster.png")):
                with self.assertRaisesRegex(pages.PagesError, "poster.png.*404"):
                    pages.verify(out / "manifest.json")

    def test_verify_retries_network_failure_and_stops_at_the_attempt_limit(self):
        with TemporaryDirectory() as directory:
            site, out = self.inputs(Path(directory))
            self.prepare(site, out)
            response = self.remote(out)
            calls = 0
            def temporary_failure(req, timeout):
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise URLError("temporary")
                return response(req, timeout)
            with patch.object(pages, "urlopen", side_effect=temporary_failure), patch.object(pages.time, "sleep") as sleep:
                result = pages.verify(out / "manifest.json", attempts=2, interval=30)
            self.assertEqual(result["attempts"], 2)
            sleep.assert_called_once_with(30)
            with patch.object(pages, "urlopen", side_effect=URLError("offline")) as http, patch.object(pages.time, "sleep") as sleep:
                with self.assertRaisesRegex(pages.PagesError, "3 次核验"):
                    pages.verify(out / "manifest.json", attempts=3, interval=0)
            self.assertEqual(http.call_count, 3)
            self.assertEqual(sleep.call_count, 2)

    def test_verify_local_changes_fail_immediately_without_network_retries(self):
        for target in ("manifest", "image"):
            with self.subTest(target=target), TemporaryDirectory() as directory:
                site, out = self.inputs(Path(directory))
                self.prepare(site, out)
                def change(name):
                    if name == "index.html":
                        path = out / ("manifest.json" if target == "manifest" else "files/poster.png")
                        path.write_bytes(path.read_bytes() + b" ")
                with patch.object(pages, "urlopen", side_effect=self.remote(out, on_request=change)), patch.object(pages.time, "sleep") as sleep:
                    with self.assertRaisesRegex(pages.PagesError, "已改变"):
                        pages.verify(out / "manifest.json", attempts=3)
                sleep.assert_not_called()

    def test_verify_wait_limits_are_checked_before_any_public_request(self):
        with patch.object(pages, "urlopen") as http:
            for attempts, interval in ((0, 30), (13, 30), (1, -1), (1, 61)):
                with self.subTest(attempts=attempts, interval=interval), self.assertRaises(pages.PagesError):
                    pages.verify("unused.json", attempts=attempts, interval=interval)
        http.assert_not_called()

    def test_custom_domain_adds_exact_cname_and_verifies_only_reader_http_files(self):
        with TemporaryDirectory() as directory:
            site, out = self.inputs(Path(directory))
            manifest = pages.prepare(site, out, "2026-10-11", "https://tokiquantlab.com/",
                                     custom_domain="tokiquantlab.com")
            self.assertEqual(manifest["custom_domain"], "tokiquantlab.com")
            self.assertEqual({item["name"] for item in manifest["files"]}, {*pages.PUBLISH_FILES, "CNAME"})
            self.assertEqual(len(manifest["files"]), 10)
            self.assertEqual((out / "files/CNAME").read_bytes(), b"tokiquantlab.com\n")
            self.assertEqual(base64.b64decode(pages.blob(out / "manifest.json", "CNAME")), b"tokiquantlab.com\n")
            html = (out / "files/index.html").read_text()
            self.assertIn('rel="canonical" href="https://tokiquantlab.com/"', html)
            with patch.object(pages, "urlopen", side_effect=self.remote(out)) as http:
                self.assertEqual(pages.verify(out / "manifest.json")["status"], "verified")
            self.assertEqual(http.call_count, 8)
            self.assertFalse(any("CNAME" in call.args[0].full_url for call in http.call_args_list))

    def test_invalid_custom_domains_and_nonmatching_root_urls_are_rejected(self):
        cases = [
            (domain, "https://example.com/") for domain in
            ["", "EXAMPLE.com", "localhost", "例子.com", "example.com/path", "example.com:443",
             "192.0.2.1", "[2001:db8::1]", "example.com\n", "-example.com", "example_.com",
             "example..com", "a" * 64 + ".com", "github.io", "example.github.io"]
        ]
        cases += [("example.com", url) for url in
                  ["https://different.com/", "https://example.com/subpath/", "https://example.com:443/"]]
        with TemporaryDirectory() as directory:
            site, out = self.inputs(Path(directory))
            for domain, url in cases:
                with self.subTest(domain=domain, url=url), self.assertRaises(pages.PagesError):
                    pages.prepare(site, out, "2026-10-11", url, custom_domain=domain)
            self.assertFalse(out.exists())

    def test_existing_cname_snapshot_cannot_be_silently_dropped(self):
        with TemporaryDirectory() as directory:
            site, out = self.inputs(Path(directory))
            self.prepare(site, out)
            self.assertNotIn("custom_domain", json.loads((out / "manifest.json").read_text()))
            pages.prepare(site, out, "2026-10-11", "https://example.com/", custom_domain="example.com")
            before = (out / "manifest.json").read_bytes()
            with self.assertRaises(pages.PagesError):
                self.prepare(site, out)
            self.assertEqual((out / "manifest.json").read_bytes(), before)
            self.assertEqual((out / "files/CNAME").read_bytes(), b"example.com\n")

    def test_cname_manifest_is_dynamic_and_its_content_must_match_the_declared_domain(self):
        for problem in ("undeclared", "missing", "wrong-content"):
            with self.subTest(problem=problem), TemporaryDirectory() as directory:
                site, out = self.inputs(Path(directory))
                pages.prepare(site, out, "2026-10-11", "https://example.com/", custom_domain="example.com")
                path = out / "manifest.json"
                manifest = json.loads(path.read_text())
                if problem == "undeclared":
                    del manifest["custom_domain"]
                elif problem == "missing":
                    manifest["files"] = [entry for entry in manifest["files"] if entry["name"] != "CNAME"]
                else:
                    content = b"other.com\n"
                    (out / "files/CNAME").write_bytes(content)
                    entry = next(item for item in manifest["files"] if item["name"] == "CNAME")
                    entry.update(size=len(content), sha256=sha256(content).hexdigest(), chunks=1)
                path.write_text(json.dumps(manifest))
                with patch.object(pages, "urlopen") as http:
                    with self.assertRaises(pages.PagesError):
                        pages.verify(path)
                http.assert_not_called()

    def test_cname_blob_requires_a_declared_custom_domain(self):
        with TemporaryDirectory() as directory:
            site, out = self.inputs(Path(directory))
            self.prepare(site, out)
            with self.assertRaisesRegex(pages.PagesError, "声明.*白名单"):
                pages.blob(out / "manifest.json", "CNAME")
