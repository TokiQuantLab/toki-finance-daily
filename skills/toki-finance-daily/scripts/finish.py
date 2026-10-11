#!/usr/bin/env python3
"""Finish an Agent-written edition using the project's existing templates."""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import struct
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from toki_finance_daily import cli
from toki_finance_daily.brief import BriefError, validate_brief
from toki_finance_daily.render import render_png

READER_FILES = (
    "public-data.json", "technical.html", "technicals.csv", "poster.html",
    "poster.png", "poster-mobile.png", "poster-cover.png", "index.html",
)
PRIVATE_OUTPUTS = (
    "poster.html", "poster.png", "poster-mobile.png", "poster-cover.png",
    "technical.html", "technicals.csv", "manifest.json",
)


def _paths(dataset_path, brief_path, out, site_out):
    dataset_path, brief_path, out, site_out = (
        Path(path).expanduser().resolve() for path in (dataset_path, brief_path, out, site_out)
    )
    if site_out == out or site_out in out.parents:
        raise BriefError("公开目录不可等于或包含私有输出目录。可使用私有输出目录下的 site 子目录。")
    if any(part.lower() in {"raw", "history"} for part in site_out.parts):
        raise BriefError("公开目录不可位于 raw 或 history 存档目录中。")
    for folder in (out, site_out):
        if folder.exists() and not folder.is_dir():
            raise BriefError(f"输出目录不是文件夹：{folder}")
    if out in site_out.parents and site_out.relative_to(out).parts[0] in (*PRIVATE_OUTPUTS, "dataset.json", "brief.json", "diagnostics"):
        raise BriefError("公开目录与私有输出文件或诊断目录冲突。")
    destinations = {out / name for name in PRIVATE_OUTPUTS}
    for original, canonical in ((dataset_path, out / "dataset.json"), (brief_path, out / "brief.json")):
        if original == site_out or site_out in original.parents or out / "site" in original.parents:
            raise BriefError("输入证据不能位于将要发布或归档的阅读站点目录中。")
        if original in destinations or (original in {out / "dataset.json", out / "brief.json"} and original != canonical):
            raise BriefError("输入文件与交付输出文件冲突。")
    return dataset_path, brief_path, out, site_out


def _inputs(dataset_bytes, brief_bytes):
    def reject_constant(value):
        raise BriefError(f"JSON 不支持非有限数值：{value}")

    dataset = json.loads(dataset_bytes, parse_constant=reject_constant)
    brief = json.loads(brief_bytes, parse_constant=reject_constant)
    if not isinstance(dataset, dict) or type(dataset.get("schema_version")) is not int or dataset["schema_version"] != 1:
        raise BriefError("Dataset 必须是 schema_version 为 1 的对象。")
    if not isinstance(dataset.get("records"), list) or not isinstance(dataset.get("sources"), list):
        raise BriefError("Dataset 的 records 和 sources 必须是数组。")
    ids = set()
    for record in dataset["records"]:
        if (not isinstance(record, dict) or not isinstance(record.get("id"), str) or not record["id"].strip()
                or record.get("kind") not in {"news", "macro", "calendar"}
                or not isinstance(record.get("data", {}), dict)):
            raise BriefError("Dataset 存在缺少 ID、类型或有效 data 对象的记录。")
        if record["id"] in ids:
            raise BriefError(f"Dataset 证据 ID 重复：{record['id']}")
        ids.add(record["id"])
    if any(not isinstance(source, dict) or not isinstance(source.get("id"), str) for source in dataset["sources"]):
        raise BriefError("Dataset 来源必须包含字符串 ID。")
    validate_brief(brief, dataset)
    return dataset, brief


def _png_size(path):
    with Path(path).open("rb") as image:
        header = image.read(24)
    if len(header) != 24 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
        raise BriefError(f"未生成有效 PNG：{Path(path).name}")
    return struct.unpack(">II", header[16:24])


def _render_cover(html_path, png_path):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as runtime:
        browser = runtime.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1080, "height": 1440}, device_scale_factor=1,
                                    locale="zh-CN", timezone_id="Asia/Shanghai")
            page.route("**/*", lambda route: route.continue_() if route.request.url.startswith(("file:", "data:")) else route.abort())
            page.goto(Path(html_path).resolve().as_uri(), wait_until="load")
            page.evaluate("document.fonts.ready")
            page.screenshot(path=str(png_path), full_page=False, animations="disabled", scale="device")
        finally:
            browser.close()


def _atomic_copy(source, target):
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".finish-", dir=target.parent)
    os.close(descriptor)
    try:
        shutil.copyfile(source, temporary)
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _sync_readers(staged, destination):
    # index.html is last, so the newly advertised files already exist.
    for name in READER_FILES:
        _atomic_copy(staged / name, destination / name)


def finish(dataset_path, brief_path, out, site_out=None):
    """Validate and stage all artifacts before archiving or updating live files."""
    dataset_path, brief_path, out, site_out = _paths(dataset_path, brief_path, out, site_out or ROOT / "artifacts/site")
    original_dataset, original_brief = dataset_path.read_bytes(), brief_path.read_bytes()
    dataset, brief = _inputs(original_dataset, original_brief)
    with tempfile.TemporaryDirectory(prefix="toki-finish-") as temporary:
        staged = Path(temporary) / "report"
        with contextlib.redirect_stdout(io.StringIO()):
            manifest = cli.render(dataset, brief, staged, width=1080, scale=1, with_workbench=True)
        html_path = staged / "poster.html"
        mobile_layout = render_png(html_path, staged / "poster-mobile.png", width=540, scale=2)
        for label, layout, image, css_width in (
            ("桌面", manifest.get("layout"), staged / "poster.png", 1080),
            ("手机", mobile_layout, staged / "poster-mobile.png", 540),
        ):
            if not isinstance(layout, dict) or not layout.get("ok") or layout.get("width") != css_width:
                raise BriefError(f"{label}长图布局检查未通过，现有交付保持原状。")
            pixel_width, pixel_height = _png_size(image)
            if pixel_width != 1080 or pixel_height < 1:
                raise BriefError(f"{label}长图像素尺寸不正确。")
        _render_cover(html_path, staged / "poster-cover.png")
        if _png_size(staged / "poster-cover.png") != (1080, 1440):
            raise BriefError("封面必须是原海报 HTML 的 1080×1440 首屏。")
        public = staged / "site"
        cli.site(dataset, brief, public, poster_folder=staged)
        if {path.name for path in public.iterdir()} != set(READER_FILES) or any(
            not (public / name).is_file() or (public / name).stat().st_size == 0 for name in READER_FILES
        ):
            raise BriefError("阅读站点文件不完整或包含白名单之外的文件。")
        # site always exports a CSV, including an empty header-only technical set.
        shutil.copyfile(public / "technicals.csv", staged / "technicals.csv")
        (staged / "dataset.json").write_bytes(original_dataset)
        (staged / "brief.json").write_bytes(original_brief)
        if dataset_path.read_bytes() != original_dataset or brief_path.read_bytes() != original_brief:
            raise BriefError("渲染期间输入文件发生变化，请使用同一版证据与成稿重新完成交付。")

        manifest.update(
            html=str(out / "poster.html"), png=str(out / "poster.png"),
            mobile_png=str(out / "poster-mobile.png"), cover_png=str(out / "poster-cover.png"),
            technical_html=str(out / "technical.html"), technical_csv=str(out / "technicals.csv"),
            site_html=str(site_out / "index.html"), public_data=str(site_out / "public-data.json"),
            mobile_layout=mobile_layout,
        )
        manifest["layout"].update(html_path=manifest["html"], png_path=manifest["png"])
        manifest["mobile_layout"].update(html_path=manifest["html"], png_path=manifest["mobile_png"])
        cli.write_json(staged / "manifest.json", manifest)

        cli.archive_outputs(out)
        for name in PRIVATE_OUTPUTS:
            if name != "manifest.json":
                _atomic_copy(staged / name, out / name)
        for name, original in (("dataset.json", dataset_path), ("brief.json", brief_path)):
            if original != out / name:
                _atomic_copy(staged / name, out / name)
        _sync_readers(public, out / "site")
        if site_out != out / "site":
            _sync_readers(public, site_out)
        _atomic_copy(staged / "manifest.json", out / "manifest.json")
    return manifest


def parser():
    command = argparse.ArgumentParser(description="完成财经日报交付：校验现有 Dataset / Brief，生成原模板长图、手机长图、封面及阅读网站。Agent 先写 Brief；本命令不调用 AI 成稿或采集。")
    command.add_argument("--dataset", required=True, type=Path, help="现有真实 Dataset JSON（演示须保持 demo 标识）")
    command.add_argument("--brief", required=True, type=Path, help="Agent 已写并引用证据的 Brief JSON")
    command.add_argument("--out", required=True, type=Path, help="私有成果目录；保留 raw / diagnostics，旧成果归档至 history")
    command.add_argument("--site-out", type=Path, default=ROOT / "artifacts/site", help="稳定公开阅读目录，默认项目 artifacts/site；仅同步八个阅读文件")
    return command


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        manifest = finish(args.dataset, args.brief, args.out, args.site_out)
    except (BriefError, OSError, ValueError, RuntimeError) as error:
        print(f"完成交付失败：{error}", file=sys.stderr)
        return 1
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
