#!/usr/bin/env python3
"""Freeze reader-only Pages uploads and verify their public deployment."""
from __future__ import annotations

import argparse
import base64
from datetime import date, datetime
from hashlib import sha256
from html import escape
from html.parser import HTMLParser
from http.client import HTTPException
from ipaddress import ip_address
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen


_spec = importlib.util.spec_from_file_location(
    "pages_finish", Path(__file__).resolve().with_name("finish.py")
)
_finish = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_finish)
READER_FILES = _finish.READER_FILES
PUBLISH_FILES = (*READER_FILES, ".nojekyll")
# Complete chunks encode without padding, so connector callers can join them.
CHUNK_SIZE = 65535


class PagesError(ValueError):
    """The publish input does not satisfy the frozen reader contract."""


class _EmbeddedData(HTMLParser):
    def __init__(self):
        super().__init__()
        self.entries = []
        self.current = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "script" and attributes.get("id") == "daily-data":
            if attributes.get("type") != "application/json" or self.current is not None:
                raise PagesError("index.html 的公开数据脚本格式不正确。")
            self.current = []

    def handle_data(self, data):
        if self.current is not None:
            self.current.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self.current is not None:
            self.entries.append("".join(self.current))
            self.current = None


def _json(content):
    def invalid_constant(value):
        raise PagesError(f"JSON 不支持非有限数值：{value}")

    try:
        return json.loads(content, parse_constant=invalid_constant)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise PagesError("公开数据或 Manifest 不是有效的 UTF-8 JSON。") from error


def _date(value):
    try:
        if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
            raise ValueError
    except ValueError as error:
        raise PagesError("日报日期必须是 YYYY-MM-DD。") from error
    return value


def _url(value):
    try:
        parts = urlsplit(value)
        if (parts.scheme != "https" or not parts.hostname or parts.username or parts.password
                or parts.query or parts.fragment or any(ord(c) < 33 for c in value)):
            raise ValueError
        _ = parts.port
    except (TypeError, ValueError) as error:
        raise PagesError("公开网址必须是无凭证的 HTTPS 页面网址。") from error
    return value


def _custom_domain(value, url):
    labels = value.split(".") if isinstance(value, str) else []
    if (not isinstance(value, str) or not value.isascii() or value != value.lower()
            or len(value) > 253 or len(labels) < 2
            or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels)
            or not re.search(r"[a-z]", labels[-1])
            or value == "github.io" or value.endswith(".github.io")):
        raise PagesError("自定义域名必须是小写 ASCII 裸 DNS 域名，不能使用 IP 或 github.io。")
    try:
        ip_address(value)
    except ValueError:
        pass
    else:
        raise PagesError("自定义域名不能使用 IP 地址。")
    address = urlsplit(_url(url))
    if address.hostname != value or address.port is not None or address.path not in {"", "/"}:
        raise PagesError("自定义域名必须与根 HTTPS 网址的 hostname 一致，不能带端口或子路径。")
    return value


def _publish_files(manifest):
    if "custom_domain" in manifest:
        _custom_domain(manifest["custom_domain"], manifest.get("url"))
        return (*PUBLISH_FILES, "CNAME")
    return PUBLISH_FILES


def _plain_file(path, *, nonempty=False):
    if path.is_symlink() or not path.is_file():
        raise PagesError(f"需要普通文件，不能使用符号链接：{path.name}")
    content = path.read_bytes()
    if nonempty and not content:
        raise PagesError(f"公开文件为空：{path.name}")
    return content


def _text(value):
    return re.sub(r"\s+", " ", value).strip() if isinstance(value, str) else ""


def _seo_index(content, public, url):
    """Add discoverable edition text to the snapshot, retaining the page template."""
    brief = public.get("brief") or {}
    lead = brief.get("lead") or {}
    title = _text(brief.get("headline")) or "Toki 财经日报"
    description = (_text(brief.get("dek")) or _text(lead.get("fact")) or title)[:180]
    cutoff = public.get("cutoff")
    try:
        if not isinstance(cutoff, str) or datetime.fromisoformat(cutoff.replace("Z", "+00:00")).utcoffset() is None:
            raise ValueError
    except ValueError as error:
        raise PagesError("公开数据的 cutoff 必须是含时区的 ISO 时间。") from error
    image = urljoin(url.rstrip("/") + "/", "poster-cover.png")
    metadata = [
        f'<meta name="description" content="{escape(description, quote=True)}">',
        f'<link rel="canonical" href="{escape(url, quote=True)}">',
    ]
    for key, value in (("title", title), ("description", description), ("type", "article"),
                       ("url", url), ("image", image)):
        metadata.append(f'<meta property="og:{key}" content="{escape(value, quote=True)}">')
    metadata.append('<meta name="twitter:card" content="summary_large_image">')
    article = {
        "@context": "https://schema.org", "@type": "Article", "headline": title,
        "description": description,
        "inLanguage": "zh-CN", "mainEntityOfPage": url, "url": url, "image": image,
        "author": {"@type": "Person", "name": "Toki"},
        "publisher": {"@type": "Organization", "name": "TokiQuantLab"},
    }
    payload = json.dumps(article, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    for character, escaped in (("&", "\\u0026"), ("<", "\\u003c"), (">", "\\u003e"),
                               ("\u2028", "\\u2028"), ("\u2029", "\\u2029")):
        payload = payload.replace(character, escaped)
    metadata.append('<script type="application/ld+json" id="daily-article">' + payload + '</script>')
    html = content.decode("utf-8")
    html, replacements = re.subn(r"</head\s*>", lambda _: "\n".join(metadata) + "\n</head>",
                                html, count=1, flags=re.IGNORECASE)
    if replacements != 1:
        raise PagesError("index.html 缺少 head，无法准备公开元信息。")

    summary = ['<noscript><section class="container section" aria-label="本期摘要">',
               '<h2>' + escape(title) + '</h2>']
    if _text(brief.get("dek")):
        summary.append('<p>' + escape(_text(brief["dek"])) + '</p>')
    if _text(lead.get("title")):
        summary.append('<h3>' + escape(_text(lead["title"])) + '</h3>')
    for key, label in (("fact", ""), ("analysis", "分析："), ("watch", "后续观察：")):
        if _text(lead.get(key)):
            summary.append('<p>' + escape(label + _text(lead[key])) + '</p>')
    for story in brief.get("stories", []):
        if _text(story.get("title")):
            summary.append('<h3>' + escape(_text(story["title"])) + '</h3>')
        if _text(story.get("fact")):
            summary.append('<p>' + escape(_text(story["fact"])) + '</p>')
    summary.append('<p><a href="public-data.json">公开财经数据</a></p></section></noscript>')
    fallback = "\n".join(summary)
    html, replacements = re.subn(r"<noscript\b[^>]*>.*?</noscript\s*>", lambda _: fallback,
                                html, count=1, flags=re.IGNORECASE | re.DOTALL)
    if not replacements:
        html, replacements = re.subn(r"</body\s*>", lambda _: fallback + "\n</body>",
                                    html, count=1, flags=re.IGNORECASE)
        if not replacements:
            raise PagesError("index.html 缺少 body，无法准备公开摘要。")
    return html.encode("utf-8")


def prepare(site, out, expected_date, url, chunk_size=CHUNK_SIZE, custom_domain=None):
    """Snapshot only finish.READER_FILES; unrelated site files are not published."""
    if type(chunk_size) is not int or chunk_size < 1:
        raise PagesError("分块大小必须是正整数。")
    url = _url(url)
    if custom_domain is not None:
        custom_domain = _custom_domain(custom_domain, url)
    if expected_date is not None:
        expected_date = _date(expected_date)
    site, out = Path(site).expanduser(), Path(out).expanduser()
    if site.is_symlink() or not site.is_dir():
        raise PagesError("公开目录必须是现有普通目录。")
    if out.is_symlink() or (out.exists() and not out.is_dir()):
        raise PagesError("快照输出必须是普通目录。")
    site, out = site.resolve(), out.resolve()
    if site == out or site in out.parents or out in site.parents:
        raise PagesError("公开目录与私有快照目录不能相同或互相包含。")
    if (any(part.lower() in {"raw", "history", "diagnostics"} for part in site.parts)
            or any((site / name).exists() for name in ("dataset.json", "brief.json"))):
        raise PagesError("不能把私有证据或存档目录作为公开目录。")

    contents = {name: _plain_file(site / name, nonempty=True) for name in READER_FILES}
    public = _json(contents["public-data.json"])
    if not isinstance(public, dict) or public.get("schema_version") != 1:
        raise PagesError("公开数据格式不正确。")
    edition_date = _date(public.get("date"))
    if public.get("mode") != "live":
        raise PagesError("仅可发布 live 日报，不能发布演示数据。")
    if expected_date is not None and edition_date != expected_date:
        raise PagesError(f"日报日期 {edition_date} 与预期日期 {expected_date} 不符。")
    parser = _EmbeddedData()
    try:
        parser.feed(contents["index.html"].decode("utf-8"))
        parser.close()
    except UnicodeError as error:
        raise PagesError("index.html 不是有效的 UTF-8 页面。") from error
    if len(parser.entries) != 1 or parser.current is not None or _json(parser.entries[0]) != public:
        raise PagesError("index.html 内嵌数据与 public-data.json 不一致。")

    contents["index.html"] = _seo_index(contents["index.html"], public, url)
    contents[".nojekyll"] = b""
    if custom_domain is not None:
        contents["CNAME"] = (custom_domain + "\n").encode("ascii")
    publish_files = tuple(contents)
    out.mkdir(parents=True, exist_ok=True)
    files = out / "files"
    if files.is_symlink() or (files.exists() and not files.is_dir()):
        raise PagesError("快照 files 必须是普通目录。")
    files.mkdir(exist_ok=True)
    if any(path.name not in publish_files or path.is_symlink() or not path.is_file()
           for path in files.iterdir()):
        raise PagesError("快照目录包含发布清单之外的文件或符号链接。")
    manifest = {
        "url": url, "date": edition_date, "mode": public["mode"],
        "timezone": public.get("timezone"), "cutoff": public.get("cutoff"),
        "chunk_size": chunk_size, "chunk_encoding": "base64",
        "files": [{"name": name, "path": str(files / name), "size": len(content),
                   "sha256": sha256(content).hexdigest(),
                   "chunks": (len(content) + chunk_size - 1) // chunk_size}
                  for name, content in contents.items()],
    }
    if custom_domain is not None:
        manifest["custom_domain"] = custom_domain
    with tempfile.TemporaryDirectory(prefix=".pages-", dir=out) as directory:
        staged = Path(directory)
        for name, content in contents.items():
            (staged / name).write_bytes(content)
        (staged / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        for name in publish_files:
            os.replace(staged / name, files / name)
        os.replace(staged / "manifest.json", out / "manifest.json")
    return manifest


def _manifest(manifest_path):
    manifest_path = Path(manifest_path).expanduser()
    content = _plain_file(manifest_path)
    manifest = _json(content)
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), list):
        raise PagesError("Manifest 缺少文件清单。")
    entries = manifest["files"]
    publish_files = _publish_files(manifest)
    if (any(not isinstance(item, dict) for item in entries)
            or any(not isinstance(item.get("name"), str) for item in entries)
            or {item["name"] for item in entries} != set(publish_files)
            or len(entries) != len(publish_files)):
        raise PagesError("Manifest 文件清单不符合固定白名单。")
    return manifest, content


def _snapshot_file(manifest_path, entry, custom_domain=None):
    manifest_path = Path(manifest_path).expanduser()
    files = manifest_path.resolve().parent / "files"
    if files.is_symlink() or not files.is_dir():
        raise PagesError("Manifest 的快照目录不正确。")
    path = files / entry["name"]
    if entry.get("path") != str(path):
        raise PagesError("Manifest 文件路径必须指向自身 files 快照。")
    content = _plain_file(path)
    if len(content) != entry.get("size") or sha256(content).hexdigest() != entry.get("sha256"):
        raise PagesError("快照文件已改变，请重新 prepare。")
    if entry["name"] == "CNAME" and (custom_domain is None or content != (custom_domain + "\n").encode("ascii")):
        raise PagesError("CNAME 快照必须只包含声明域名与换行。")
    return content


def blob(manifest_path, name, offset=0, size=None):
    """Encode one raw-byte slice; default complete chunks have no base64 padding."""
    if name not in (*PUBLISH_FILES, "CNAME"):
        raise PagesError("文件名不在固定发布白名单内。")
    manifest, _ = _manifest(manifest_path)
    if name not in _publish_files(manifest):
        raise PagesError("文件名不在本次声明的发布白名单内。")
    entry = next(item for item in manifest["files"] if item["name"] == name)
    content = _snapshot_file(manifest_path, entry, manifest.get("custom_domain"))
    if size is None:
        size = manifest.get("chunk_size")
    if (type(offset) is not int or offset < 0 or offset > len(content)
            or type(size) is not int or size < 1):
        raise PagesError("offset 必须位于文件内，size 必须是正整数。")
    return base64.b64encode(content[offset:offset + size]).decode("ascii")


def _embedded(content):
    reader = _EmbeddedData()
    try:
        reader.feed(content.decode("utf-8"))
        reader.close()
    except UnicodeError as error:
        raise PagesError("页面不是有效的 UTF-8。") from error
    if len(reader.entries) != 1 or reader.current is not None:
        raise PagesError("页面缺少唯一的 daily-data。")
    return _json(reader.entries[0])


def _snapshot(manifest_path, fingerprint=None):
    manifest, content = _manifest(manifest_path)
    digest = sha256(content).hexdigest()
    if fingerprint is not None and digest != fingerprint:
        raise PagesError("Manifest 已改变，请使用原发布快照重新核验。")
    files = {entry["name"]: _snapshot_file(manifest_path, entry, manifest.get("custom_domain"))
             for entry in manifest["files"]}
    return manifest, files, digest


def _remote_files(manifest, expected):
    """Fetch one immutable edition; errors here are eligible for bounded retry."""
    url = _url(manifest.get("url"))
    entries = {item["name"]: item for item in manifest["files"]}

    def fetch(name):
        address = urljoin(url.rstrip("/") + "/", name)
        request = Request(address, headers={"User-Agent": "TokiFinanceDaily-PagesVerify", "Cache-Control": "no-cache"})
        try:
            with urlopen(request, timeout=20) as response:
                _url(response.geturl())
                return response.read(entries[name]["size"] + 1)
        except HTTPError as error:
            raise PagesError(f"线上 {name} 暂不可用：HTTP {error.code}。") from error
        except (URLError, OSError, HTTPException) as error:
            raise PagesError(f"暂时无法获取线上 {name}。") from error

    index, data = fetch("index.html"), fetch("public-data.json")
    public = _json(expected["public-data.json"])
    if _embedded(index) != public or _json(data) != public:
        raise PagesError("线上 HTML 与公开 JSON 尚未完整更新到本次快照。")
    downloaded = {"index.html": index, "public-data.json": data}
    for name in READER_FILES:
        content = downloaded[name] if name in downloaded else fetch(name)
        if sha256(content).hexdigest() != entries[name]["sha256"]:
            raise PagesError(f"线上 {name} 尚未匹配本次快照。")


def verify(manifest_path, attempts=1, interval=30):
    """Confirm every public file, without modifying the snapshot or publishing."""
    if type(attempts) is not int or not 1 <= attempts <= 12:
        raise PagesError("核验尝试次数必须在 1–12 之间。")
    if not isinstance(interval, (int, float)) or not 0 <= interval <= 60:
        raise PagesError("核验间隔必须在 0–60 秒之间。")
    manifest, expected, fingerprint = _snapshot(manifest_path)
    _url(manifest.get("url"))
    if _embedded(expected["index.html"]) != _json(expected["public-data.json"]):
        raise PagesError("本地 HTML 与公开 JSON 快照不一致。")
    last_error = None
    for attempt in range(1, attempts + 1):
        _snapshot(manifest_path, fingerprint)
        try:
            _remote_files(manifest, expected)
            last_error = None
        except PagesError as error:
            last_error = error
        # Changes to local input are final failures, including after a network error.
        _snapshot(manifest_path, fingerprint)
        if last_error is None:
            return {"status": "verified", "url": manifest["url"], "date": manifest.get("date"),
                    "cutoff": manifest.get("cutoff"), "files": len(READER_FILES), "attempts": attempt}
        if attempt < attempts:
            time.sleep(interval)
    raise PagesError(f"{attempts} 次核验后仍未确认线上完整更新：{last_error}")


def parser():
    command = argparse.ArgumentParser(description="准备公开站点快照与 GitHub connector 分块输入；verify 仅通过公开 GET 核验，不推送。")
    subcommands = command.add_subparsers(dest="command", required=True)
    collect = subcommands.add_parser("prepare", help="校验并快照八个公开文件与 .nojekyll")
    collect.add_argument("--site", required=True, type=Path)
    collect.add_argument("--out", required=True, type=Path)
    collect.add_argument("--expected-date")
    collect.add_argument("--url", required=True)
    collect.add_argument("--cname", help="可选的小写裸域名，必须对应根 HTTPS 网址")
    collect.add_argument("--chunk-size", type=int, default=CHUNK_SIZE)
    read = subcommands.add_parser("blob", help="输出原文件字节分块的 base64，无换行")
    read.add_argument("--manifest", required=True, type=Path)
    read.add_argument("--name", required=True)
    read.add_argument("--offset", type=int, default=0)
    read.add_argument("--size", type=int)
    check = subcommands.add_parser("verify", help="公开 GET 核验线上完整快照，不重新准备或发布")
    check.add_argument("--manifest", required=True, type=Path)
    check.add_argument("--attempts", type=int, default=1)
    check.add_argument("--interval", type=float, default=30)
    return command


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "prepare":
            manifest = prepare(args.site, args.out, args.expected_date, args.url, args.chunk_size, args.cname)
            print(json.dumps(manifest, ensure_ascii=False, indent=2))
        elif args.command == "blob":
            sys.stdout.write(blob(args.manifest, args.name, args.offset, args.size))
        else:
            print(json.dumps(verify(args.manifest, args.attempts, args.interval), ensure_ascii=False))
    except (PagesError, OSError) as error:
        action = "核验" if args.command == "verify" else "准备"
        print(f"Pages {action}失败：{error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
