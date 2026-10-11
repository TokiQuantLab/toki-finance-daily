"""Small composable commands for Agent and unattended API workflows."""
from __future__ import annotations

import argparse
import json
import math
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from .brief import BRIEF_SCHEMA, BriefError, editorial_prompt, generate_brief, make_packet, resource_path, validate_brief


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def archive_outputs(folder, full_run=False):
    """Preserve previous results before a new run can make their names misleading."""
    folder = Path(folder)
    names = ["poster.html", "poster.png", "poster-mobile.png", "poster-cover.png", "technical.html", "technicals.csv", "site", "manifest.json"]
    if not full_run and not any((folder / name).exists() for name in names[:-1]):
        return
    if full_run:
        names += ["dataset.json", "brief.json", "packet.json", "prompt.md", "brief.schema.json", "editorial-notes.md", "raw", "audit"]
    existing = [folder / name for name in names if (folder / name).exists()]
    if not existing:
        return
    destination = folder / "history" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination.mkdir(parents=True)
    for path in existing:
        shutil.move(str(path), destination / path.name)
    if not full_run:
        for name in ("dataset.json", "brief.json"):
            if (folder / name).is_file():
                shutil.copyfile(folder / name, destination / name)
    manifest = destination / "manifest.json"
    if manifest.is_file():
        try:
            previous = read_json(manifest)
        except ValueError:
            return
        if not isinstance(previous, dict):
            return
        for key in ("html", "png", "mobile_png", "cover_png", "technical_html", "technical_csv"):
            if previous.get(key):
                target = destination / Path(previous[key]).name
                if target.is_file():
                    previous[key] = str(target.resolve())
        for key in ("site_html", "public_data"):
            if previous.get(key):
                target = destination / "site" / Path(previous[key]).name
                if target.is_file():
                    previous[key] = str(target.resolve())
        if isinstance(previous.get("layout"), dict):
            previous["layout"].update(html_path=previous.get("html"), png_path=previous.get("png"))
        if isinstance(previous.get("mobile_layout"), dict):
            previous["mobile_layout"].update(html_path=previous.get("html"), png_path=previous.get("mobile_png"))
        write_json(manifest, previous)


def collect_into(args, folder):
    from .collect import collect
    archive_outputs(folder, full_run=True)
    write_json(Path(folder) / "manifest.json", {"status": "collecting", "html": None, "png": None})
    dataset = collect(args.sources, date=args.date, hours=args.hours, cutoff=args.cutoff, out_dir=folder,
                      calendar_lookback_days=args.calendar_lookback_days)
    write_json(Path(folder) / "dataset.json", dataset)
    write_json(Path(folder) / "manifest.json", {"status": "collected", "date": dataset["date"], "cutoff": dataset["cutoff"], "html": None, "png": None})
    counts = {}
    for r in dataset["records"]:
        counts[r["kind"]] = counts.get(r["kind"], 0) + 1
    print(json.dumps({"dataset": str((Path(folder) / "dataset.json").resolve()), "records": counts, "sources": [{"id": s["id"], "status": s["status"], "count": s["count"], "error": s.get("error")} for s in dataset["sources"]]}, ensure_ascii=False, indent=2))
    if not dataset["records"]:
        raise BriefError("本次没有可用证据，查看来源状态后重试。")
    return dataset


def prepare(dataset, folder, summary_chars=550):
    packet = make_packet(dataset, summary_chars=summary_chars)
    folder = Path(folder)
    write_json(folder / "packet.json", packet)
    write_json(folder / "brief.schema.json", BRIEF_SCHEMA)
    (folder / "prompt.md").write_text(editorial_prompt(packet), encoding="utf-8")
    print(json.dumps({"packet": str((folder / "packet.json").resolve()), "prompt": str((folder / "prompt.md").resolve()), "selected": packet["packet_info"]["selected"], "available": packet["packet_info"]["available"], "next": "AI 按 prompt.md 写 brief.json，然后运行 render。"}, ensure_ascii=False, indent=2))


def workbench(folder):
    """Create the online companion with provider-owned, attributed widgets."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    destination = folder / "technical.html"
    shutil.copyfile(Path(__file__).parent / "assets" / "workbench.html", destination)
    return str(destination.resolve())


def site(dataset, brief, folder, poster_folder=None):
    """Create a static public directory containing only reader-facing artifacts."""
    from .site import build_site
    from .technical_export import export_technicals
    validate_brief(brief, dataset)
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    if poster_folder:
        for name in ("poster.html", "poster.png", "poster-mobile.png", "poster-cover.png"):
            original = Path(poster_folder) / name
            target = folder / name
            if original.is_file() and original.resolve() != target.resolve():
                shutil.copyfile(original, target)
            elif not original.is_file() and target.is_file():
                target.unlink()
    workbench(folder)
    export_technicals(dataset, folder / "technicals.csv")
    return build_site(dataset, brief, folder)


def render(dataset, brief, folder, html_only=False, width=1080, scale=None, with_workbench=False, with_site=False):
    from .render import render_html, render_png
    validate_brief(brief, dataset)
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    archive_outputs(folder)
    write_json(folder / "dataset.json", dataset)
    write_json(folder / "brief.json", brief)
    html_path, png_path = folder / "poster.html", folder / "poster.png"
    html_path.write_text(render_html(brief, dataset), encoding="utf-8")
    manifest = {"status": "ready", "mode": dataset["mode"], "date": dataset["date"], "cutoff": dataset["cutoff"], "html": str(html_path.resolve()), "png": None, "layout": None}
    if not html_only:
        manifest["layout"] = render_png(html_path, png_path, width=width, scale=scale)
        if not manifest["layout"].get("ok"):
            write_json(folder / "manifest.json", {**manifest, "status": "layout_error"})
            raise BriefError("海报布局存在溢出或裁切，检查 manifest.json 后修正。")
        manifest["png"] = str(png_path.resolve())
    if with_workbench:
        manifest["technical_html"] = workbench(folder)
    if any(record.get("data", {}).get("topic") == "technical" for record in dataset.get("records", [])):
        from .technical_export import export_technicals
        exported = export_technicals(dataset, folder / "technicals.csv")
        manifest["technical_csv"] = exported["path"]
    if with_site:
        website = site(dataset, brief, folder / "site", poster_folder=folder)
        manifest.update(site_html=website["html"], public_data=website["public_data"])
    write_json(folder / "manifest.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return manifest


def parser():
    p = argparse.ArgumentParser(prog="toki-daily", description="Toki Quant Lab · 采集、AI 编辑与财经日报长图")
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("collect", "run"):
        cmd = sub.add_parser(name, help="采集证据" if name == "collect" else "采集 → 模型成稿 → 海报")
        cmd.add_argument("--sources", type=Path, default=resource_path("sources"))
        cmd.add_argument("--date", help="Asia/Shanghai 日报日期 YYYY-MM-DD，默认今天")
        cmd.add_argument("--cutoff", help="含时区的 ISO 截止时间，默认现在；历史日报需要准确截止时间")
        cmd.add_argument("--hours", type=float, default=48, help="新闻回看小时数，默认 48")
        cmd.add_argument("--calendar-lookback-days", type=int, help="日历历史回看天数 0–31，默认采用来源配置")
        cmd.add_argument("--out", type=Path, default=Path("artifacts/live"))
        if name == "run":
            cmd.add_argument("--html-only", action="store_true")
            cmd.add_argument("--width", type=int, default=1080, help="排版宽度，默认 1080；手机单列可用 540")
            cmd.add_argument("--scale", type=float, help="导出像素倍率 1–4；默认手机宽度≤700 用 2 倍，其余用 1 倍")
            cmd.add_argument("--enrich-limit", type=int, default=8, help="成稿前补采正文数量，0 为关闭，最多 24")
            cmd.add_argument("--with-workbench", action="store_true", help="同时生成含免费在线组件的技术观察页 technical.html")
            cmd.add_argument("--with-site", action="store_true", help="同时生成完整静态财经网站 site/index.html")
    cmd = sub.add_parser("prepare", help="压缩证据并输出 Agent 成稿提示与契约")
    cmd.add_argument("--dataset", required=True, type=Path)
    cmd.add_argument("--out", required=True, type=Path)
    cmd.add_argument("--summary-chars", type=int, default=550, help="每条证据摘要最多字符数，550–4000")
    cmd = sub.add_parser("enrich", help="为选中的新闻补采正文和原始快照")
    cmd.add_argument("--dataset", required=True, type=Path)
    cmd.add_argument("--ids", help="逗号分隔的新闻证据 ID；未指定时按来源轮选候选")
    cmd.add_argument("--limit", type=int, default=8, help="默认补采 8 条，最多 24")
    cmd.add_argument("--out", required=True, type=Path)
    for name in ("validate", "render"):
        cmd = sub.add_parser(name, help="检查成稿契约" if name == "validate" else "成稿 → HTML / PNG 海报")
        cmd.add_argument("--dataset", required=True, type=Path)
        cmd.add_argument("--brief", required=True, type=Path)
        if name == "render":
            cmd.add_argument("--out", type=Path, default=Path("artifacts/live"))
            cmd.add_argument("--html-only", action="store_true")
            cmd.add_argument("--width", type=int, default=1080, help="排版宽度，默认 1080；手机单列可用 540")
            cmd.add_argument("--scale", type=float, help="导出像素倍率 1–4；默认手机宽度≤700 用 2 倍，其余用 1 倍")
            cmd.add_argument("--with-workbench", action="store_true", help="同时生成含免费在线组件的技术观察页 technical.html")
            cmd.add_argument("--with-site", action="store_true", help="同时生成完整静态财经网站 site/index.html")
    cmd = sub.add_parser("workbench", help="生成免费在线技术评级与财经日历配套网页")
    cmd.add_argument("--out", type=Path, default=Path("artifacts/technical-workbench"))
    cmd = sub.add_parser("export-technicals", help="将技术评级、指标、均线和枢轴点导出为研究 CSV")
    cmd.add_argument("--dataset", required=True, type=Path)
    cmd.add_argument("--out", required=True, type=Path, help="CSV 文件路径")
    cmd = sub.add_parser("cache-technicals", help="校验正常浏览取得的可见技术 HTML 并保存每日缓存")
    cmd.add_argument("--sources", type=Path, default=resource_path("sources"))
    cmd.add_argument("--source-id", required=True)
    cmd.add_argument("--html", required=True, type=Path)
    cmd.add_argument("--observed-at", required=True, help="实际页面读取时刻，含时区的 ISO 时间")
    cmd = sub.add_parser("site", help="生成含完整财经资料与技术明细的静态研究网站")
    cmd.add_argument("--dataset", required=True, type=Path)
    cmd.add_argument("--brief", required=True, type=Path)
    cmd.add_argument("--out", required=True, type=Path, help="站点公开目录")
    cmd = sub.add_parser("demo", help="运行标记为演示的离线版式样张")
    cmd.add_argument("--out", type=Path, default=Path("artifacts/demo"))
    cmd.add_argument("--html-only", action="store_true")
    cmd.add_argument("--width", type=int, default=1080)
    cmd.add_argument("--scale", type=float, help="导出像素倍率 1–4；默认手机用 2 倍，其余用 1 倍")
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command in {"run", "render", "demo"} and not 320 <= args.width <= 4096:
            raise BriefError("--width 应在 320 到 4096 像素之间。")
        if args.command in {"run", "render", "demo"} and args.scale is not None and (not math.isfinite(args.scale) or not 1 <= args.scale <= 4):
            raise BriefError("--scale 应为 1 到 4 之间的有限数值。")
        if args.command == "run" and not 0 <= args.enrich_limit <= 24:
            raise BriefError("--enrich-limit 应在 0 到 24 之间。")
        if args.command in {"collect", "run"}:
            if not 1 <= args.hours <= 168:
                raise BriefError("--hours 应在 1 到 168 小时之间。")
            dataset = collect_into(args, args.out)
            if args.command == "run":
                if args.enrich_limit:
                    from .enrich import enrich_dataset
                    dataset = enrich_dataset(dataset, limit=args.enrich_limit, out_dir=args.out)
                    write_json(args.out / "dataset.json", dataset)
                prepare(dataset, args.out, summary_chars=1800)
                brief = generate_brief(dataset, args.out / "audit")
                write_json(args.out / "brief.json", brief)
                render(dataset, brief, args.out, args.html_only, args.width, args.scale, args.with_workbench, args.with_site)
        elif args.command == "prepare":
            if not 550 <= args.summary_chars <= 4000:
                raise BriefError("--summary-chars 应在 550 到 4000 之间。")
            prepare(read_json(args.dataset), args.out, args.summary_chars)
        elif args.command == "enrich":
            from .enrich import enrich_dataset
            if not 1 <= args.limit <= 24:
                raise BriefError("--limit 应在 1 到 24 之间。")
            dataset = read_json(args.dataset)
            ids = [item.strip() for item in args.ids.split(",") if item.strip()] if args.ids else None
            dataset = enrich_dataset(dataset, ids=ids, limit=args.limit, out_dir=args.out)
            archive_outputs(args.out)
            write_json(args.out / "dataset.json", dataset)
            write_json(args.out / "manifest.json", {"status": "enriched", "date": dataset["date"], "cutoff": dataset["cutoff"], "html": None, "png": None})
            print(json.dumps({"dataset": str((args.out / "dataset.json").resolve()), "enrichment": dataset.get("enrichment", [])}, ensure_ascii=False, indent=2))
        elif args.command in {"validate", "render"}:
            dataset, brief = read_json(args.dataset), read_json(args.brief)
            validate_brief(brief, dataset)
            if args.command == "validate":
                print("成稿契约通过；引用存在、元数据一致、日历时间匹配。事实语义仍需编辑核对。")
            else:
                render(dataset, brief, args.out, args.html_only, args.width, args.scale, args.with_workbench, args.with_site)
        elif args.command == "workbench":
            print(json.dumps({"technical_html": workbench(args.out), "requires_network": True}, ensure_ascii=False, indent=2))
        elif args.command == "export-technicals":
            from .technical_export import export_technicals
            print(json.dumps(export_technicals(read_json(args.dataset), args.out), ensure_ascii=False, indent=2))
        elif args.command == "cache-technicals":
            from .page_cache import admit_browser_snapshot
            config = read_json(args.sources)
            matches = [source for source in config["sources"] if source["id"] == args.source_id]
            if len(matches) != 1:
                raise BriefError("--source-id 必须唯一对应来源配置。")
            if args.html.stat().st_size > int(config.get("network", {}).get("max_bytes", 3000000)):
                raise BriefError("可见页面快照超过来源配置的大小限制。")
            _, metadata = admit_browser_snapshot(matches[0], config.get("network", {}),
                                                  args.html.read_bytes(), args.observed_at)
            print(json.dumps({"source_id": args.source_id, "observed_at": metadata["fetched_at"],
                              "transport": metadata["transport"]}, ensure_ascii=False, indent=2))
        elif args.command == "site":
            result = site(read_json(args.dataset), read_json(args.brief), args.out, poster_folder=args.dataset.parent)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == "demo":
            args.out.mkdir(parents=True, exist_ok=True)
            archive_outputs(args.out, full_run=True)
            for name, resource in (("dataset.json", "demo_dataset"), ("brief.json", "demo_brief")):
                shutil.copyfile(resource_path(resource), args.out / name)
            render(read_json(args.out / "dataset.json"), read_json(args.out / "brief.json"), args.out, args.html_only, args.width, args.scale)
        return 0
    except (BriefError, OSError, ValueError, RuntimeError) as exc:
        if args.command in {"collect", "run", "demo", "render"}:
            try:
                failure = {"status": "failed", "error": str(exc), "html": None, "png": None}
                current_manifest = args.out / "manifest.json"
                if current_manifest.is_file():
                    previous = read_json(current_manifest)
                    if isinstance(previous, dict) and previous.get("status") == "layout_error":
                        failure = {**previous, **failure}
                write_json(current_manifest, failure)
            except (OSError, ValueError):
                pass
        print("错误：" + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
