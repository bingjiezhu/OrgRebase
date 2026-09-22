"""Build a static reading site from the repository's existing Markdown sources.

Only the documented source files are read. Linked source/evidence paths get local
reference pages; the site never redirects them to an unrelated remote revision.
"""

from __future__ import annotations

# Generated Chinese prose intentionally uses Chinese punctuation.
# ruff: noqa: RUF001
import argparse
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from importlib import metadata
from pathlib import Path
from urllib.parse import unquote, urlsplit

import yaml
from markdown.extensions.toc import slugify_unicode

ROOT = Path(__file__).resolve().parents[1]
ROOT_PAGES = (
    "README.md",
    "README.zh-CN.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "CODE_OF_CONDUCT.md",
    "COMMERCIAL-LICENSE.md",
    "NOTICE.md",
    "CHANGELOG.md",
    "RELEASE-VERIFICATION.md",
    "LICENSE",
    "LICENSE.md",
)
PUBLIC_SOURCE_DIRS = {
    "src",
    "scripts",
    "configs",
    "schemas",
    "contracts",
    "skills",
    "examples",
    "fixtures",
    "agentteams",
    "benchmark",
    "tests",
}
TEXT_SUFFIXES = {".py", ".json", ".yaml", ".yml", ".toml", ".md", ".txt", ".sh"}
LINK = re.compile(r"(!?\[[^\]\n]*\]\()([^\s)]+)(\))")
HREF = re.compile(r'(href|src)="([^"\n]+)"')


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", required=True, type=Path, help="New, empty output directory outside the source checkout"
    )
    parser.add_argument(
        "--site-url", help="Canonical HTTPS URL only when the deployment address has been chosen"
    )
    parser.add_argument("--source-archive", type=Path, help="Reviewed source ZIP to include as a download")
    parser.add_argument("--source-sha256", help="Expected SHA-256 of the reviewed source ZIP")
    args = parser.parse_args()
    if bool(args.source_archive) != bool(args.source_sha256):
        parser.error("source archive and expected SHA-256 must be supplied together")
    archive_bytes = None
    if args.source_archive:
        if args.source_archive.is_symlink():
            parser.error("source archive must be a regular, non-symlink file")
        archive_bytes = args.source_archive.read_bytes()
        if sha(archive_bytes) != args.source_sha256:
            parser.error("source archive SHA-256 mismatch")
        if not zipfile.is_zipfile(args.source_archive):
            parser.error("source archive must be a ZIP")
    output = args.output.resolve()
    if output.is_relative_to(ROOT) or ROOT.is_relative_to(output):
        parser.error("output must be outside the source checkout")
    if output.exists() and any(output.iterdir()):
        parser.error("output directory must be empty; use a new path to preserve earlier builds")
    if args.site_url:
        canonical = urlsplit(args.site_url)
        if (
            canonical.scheme != "https"
            or not canonical.netloc
            or canonical.username
            or canonical.password
            or canonical.query
            or canonical.fragment
        ):
            parser.error("site URL must be an HTTPS address without credentials, query or fragment")

    mapping = {
        name: {
            "README.md": "reference/readme-english.md",
            "README.zh-CN.md": "reference/readme-chinese.md",
        }.get(name, name)
        for name in ROOT_PAGES
    }
    for path in sorted((ROOT / "docs").rglob("*.md")):
        if path.is_symlink():
            parser.error("symlinked documentation is not accepted")
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith("docs/guide/"):
            name = path.name
            mapping[rel] = name if name.startswith("index.") else "guides/" + name
        else:
            mapping[rel] = "reference/document-index.md" if rel == "docs/README.md" else rel
    for path in (ROOT / "docs/guide").glob("*.md"):
        counterpart = (
            path.name.replace(".zh.md", ".en.md")
            if path.name.endswith(".zh.md")
            else path.name.replace(".en.md", ".zh.md")
        )
        if counterpart == path.name or not path.with_name(counterpart).is_file():
            parser.error("missing paired core guide: " + path.name)
    records = []
    references = {}
    with tempfile.TemporaryDirectory(prefix="orgrebase-docs-") as temp:
        work = Path(temp)
        pages = work / "pages"
        pages.mkdir()

        def reference(target: Path) -> str:
            try:
                rel = target.relative_to(ROOT).as_posix()
            except ValueError:
                # Cross-repository references remain named, never read from the host.
                rel = os.path.relpath(target, ROOT)
            identifier = sha(rel.encode())[:20]
            path = f"source-reference/{identifier}.md"
            if path in references:
                return path
            exists = target.is_file() and not target.is_symlink() and target.is_relative_to(ROOT)
            readable = (
                exists
                and target.relative_to(ROOT).parts[0] in PUBLIC_SOURCE_DIRS
                and target.suffix in TEXT_SUFFIXES
                and target.stat().st_size <= 160_000
            )
            body = f"# 源码资源 / Source resource\n\nRepository path / 仓库相对路径：`{rel}`\n\n"
            body += "此引用对应生成本站时的源码工作区，不跳转到可能不同的远端 main。 / This reference belongs to the local source snapshot, not a potentially different remote main revision.\n\n"
            if readable:
                raw = target.read_bytes()
                try:
                    content = raw.decode("utf-8")
                except UnicodeDecodeError:
                    readable = False
                else:
                    body += f"文件 SHA-256：`{sha(raw)}`\n\n"
                    fence = "`" * max(3, max((len(x) for x in re.findall(r"`+", content)), default=0) + 1)
                    body += f"{fence}{'python' if target.suffix == '.py' else 'text'}\n{content}\n{fence}\n"
                    records.append({"path": rel, "sha256": sha(raw), "kind": "linked-source"})
            if not readable:
                body += (
                    "该目录、较大文件或历史证据未由文档站分发。请在与所需验证范围匹配的源码/证据包中查看此路径；"
                    "轻量运行包可能不含完整历史档案。文件链接本身不证明已安装或已验证。 / This directory, large file or historical resource is not distributed by the documentation site; use the matching source/evidence package. A link alone does not prove installation or validation.\n"
                )
            references[path] = {"path": rel, "inline_source": readable}
            (pages / path).parent.mkdir(parents=True, exist_ok=True)
            (pages / path).write_text("---\nsearch:\n  exclude: true\n---\n\n" + body, encoding="utf-8")
            return path

        def destination(url: str, original: str, rendered: str, *, raw_html: bool = False) -> str:
            parsed = urlsplit(html.unescape(url))
            if parsed.scheme or parsed.netloc or not parsed.path:
                return url
            target = (ROOT / original).parent.joinpath(unquote(parsed.path)).resolve()
            try:
                rel = target.relative_to(ROOT).as_posix()
            except ValueError:
                rel = ""
            if rel in mapping:
                dest = re.sub(r"\.(zh|en)\.md$", ".md", mapping[rel])
            elif (
                rel.startswith("docs/diagrams/")
                and target.suffix == ".html"
                and target.is_file()
                and not target.is_symlink()
            ):
                dest = rel
                (pages / dest).parent.mkdir(parents=True, exist_ok=True)
                diagram = target.read_bytes()
                (pages / dest).write_text(
                    re.sub(
                        r'<link[^>]+href="https://fonts\.googleapis\.com/[^>]+>', "", diagram.decode("utf-8")
                    ),
                    encoding="utf-8",
                )
                if not any(record["path"] == rel for record in records):
                    records.append({"path": rel, "sha256": sha(diagram), "kind": "document-asset"})
            else:
                dest = reference(target)

            def page_url(name: str) -> Path:
                path = Path(name)
                if path.suffix != ".md":
                    return path
                if path.name in {"README.md", "index.md"}:
                    return path.parent / "index.html"
                return path.with_suffix("") / "index.html"

            link = (
                os.path.relpath(page_url(dest), page_url(rendered).parent).replace(os.sep, "/")
                if raw_html
                else os.path.relpath(dest, Path(rendered).parent).replace(os.sep, "/")
            )
            return link + ("#" + parsed.fragment if parsed.fragment and rel in mapping else "")

        for original, rendered in mapping.items():
            path = ROOT / original
            if not path.is_file() or path.is_symlink():
                raise ValueError(f"Required documentation missing: {original}")
            raw = path.read_bytes()
            records.append({"path": original, "sha256": sha(raw), "kind": "document"})
            text = raw.decode("utf-8")
            if not original.startswith("docs/guide/"):
                text = (
                    '!!! note "原文参考 / Original-language reference"\n\n'
                    "    本页保留原始语言，尚非逐页中英对应版本。 / This page retains its source language; "
                    "it is not part of the fully paired core guides.\n\n" + text
                )
            # A remote CI badge describes remote HEAD, not this local content snapshot.
            text = re.sub(r"^\[!\[CI\].*\n", "", text, flags=re.M)
            text = LINK.sub(
                lambda m, original=original, rendered=rendered: (
                    m[1] + destination(m[2], original, rendered) + m[3]
                ),
                text,
            )
            text = HREF.sub(
                lambda m, original=original, rendered=rendered: (
                    f'{m[1]}="{destination(m[2], original, rendered, raw_html=True)}"'
                ),
                text,
            )
            dest = pages / rendered
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(text, encoding="utf-8")
        license_lines = [
            "# 文档站工具链与第三方许可",
            "",
            "本站使用MkDocs/Material生成静态页面。这些工具的许可不改变OrgRebase软件、OAC或数据的许可。",
            "以下为独立文档环境的已安装发行包版本及其自带许可副本；不是业务运行环境SBOM。",
            "静态资源原有的版权声明保持不变。",
            "",
            "| 发行包 | 版本 | 原文许可/声明 |",
            "|---|---|---|",
        ]
        for distribution in sorted(metadata.distributions(), key=lambda d: d.metadata["Name"].lower()):
            name = distribution.metadata["Name"]
            links = []
            for index, item in enumerate(distribution.files or []):
                if not item.name.upper().startswith(("LICENSE", "COPYING", "NOTICE")):
                    continue
                source = Path(distribution.locate_file(item))
                if not source.is_file():
                    continue
                relative = f"assets/licenses/{name}/{index}-{item.name}"
                (pages / relative).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, pages / relative)
                links.append(f"[原文{len(links) + 1}]({relative})")
            license_lines.append(
                f"| {name} | {distribution.version} | {', '.join(links) or '发行包未附独立许可文件；需查看上游条款'} |"
            )
        (pages / "site-third-party.zh.md").write_text("\n".join(license_lines) + "\n", encoding="utf-8")
        english_licenses = [
            "# Documentation toolchain licenses",
            "",
            "MkDocs and Material generate this static site. Their licenses do not change OrgRebase, OAC or data terms.",
            "The table records installed packages in the separate documentation environment and their bundled license files; it is not a business-runtime SBOM.",
            "Existing copyright notices in static assets remain intact.",
            "",
            "| Distribution | Version | Original license/notice |",
            "|---|---|---|",
        ]
        for row in license_lines[8:]:
            english_licenses.append(
                row.replace("原文", "Text ").replace(
                    "发行包未附独立许可文件；需查看上游条款",
                    "No standalone license file in this distribution; consult upstream terms",
                )
            )
        (pages / "site-third-party.en.md").write_text("\n".join(english_licenses) + "\n", encoding="utf-8")
        css = (ROOT / "documentation/site.css").read_bytes()
        (pages / "assets").mkdir(exist_ok=True)
        (pages / "assets/site.css").write_bytes(css)
        for name in (
            "documentation/build.py",
            "documentation/mkdocs.yml",
            "documentation/pyproject.toml",
            "documentation/uv.lock",
            "documentation/site.css",
            "documentation/overrides/main.html",
            ".github/workflows/docs.yml",
        ):
            records.append({"path": name, "sha256": sha((ROOT / name).read_bytes()), "kind": "site-tooling"})
        records.sort(key=lambda r: r["path"])
        digest = sha(json.dumps(records, sort_keys=True, separators=(",", ":")).encode())
        identity = {
            "schema_version": "orgrebase.documentation-snapshot.v1",
            "content_sha256": digest,
            "scope": "DOCUMENTS_LINKED_SOURCE_AND_SITE_TOOLING_NOT_COMPLETE_PRODUCT",
            "files": records,
            "source_references": references,
            "remote_publication_claimed": False,
        }
        download = ""
        if archive_bytes is not None:
            with zipfile.ZipFile(args.source_archive) as archive:
                for record in records:
                    if record["kind"] not in {"document", "document-asset", "site-tooling", "linked-source"}:
                        continue
                    member = "source-snapshot/orgrebase/" + record["path"]
                    if sha(archive.read(member)) != record["sha256"]:
                        raise ValueError("Documentation source differs from download: " + record["path"])
            downloads = pages / "downloads"
            downloads.mkdir()
            (downloads / "orgrebase-source.zip").write_bytes(archive_bytes)
            (downloads / "SHA256SUMS.txt").write_text(args.source_sha256 + "  orgrebase-source.zip\n")
            identity["source_archive"] = {
                "sha256": args.source_sha256,
                "bytes": len(archive_bytes),
                "document_source_binding": "MATCHED",
                "linked_source_binding": "MATCHED",
            }
            download = (
                "## 下载对应源码\n\n[下载源码ZIP](downloads/orgrebase-source.zip) · "
                "[SHA-256清单](downloads/SHA256SUMS.txt)\n\n"
                f"SHA-256：`{args.source_sha256}`。大小：{len(archive_bytes):,}字节。\n\n"
                "本站文档、引用源码与构建工具的源字节已与此包逐项比对。下载不等于客户部署或生产验收。\n\n"
            )
        (pages / "source-manifest.json").write_text(json.dumps(identity, ensure_ascii=False, indent=2) + "\n")
        (pages / "source-version.zh.md").write_text(
            "# 本站源码与版本\n\n"
            + download
            + "本站直接从同一源码工作区的 README、用户文档、贡献与许可文件生成；核心指南逐页中英配对，详细资料标明原文语言；每种语言只有一份维护源。\n\n"
            f"文档、引用源码与站点工具链的内容摘要：`{digest}`。这不是整个产品的源码摘要或发布资格。\n\n"
            "[逐文件来源清单](source-manifest.json)说明本站实际读取了哪些字节。站内源码引用展示同一构建读取的文本；"
            "未分发的历史或大文件会明确提示，不替换成远端旧版本。\n\n"
            "仓库 main、已发布版本与本站快照可能不同。复现时使用对应源码ZIP和SHA清单，"
            "或取得相同的精确源码版本。分发包与本站源码下载应使用同一ZIP与摘要。\n\n"
            "OrgRebase 项目自有材料与 OAC 使用同一套路径许可（Apache-2.0 与 CC BY 4.0）；建站不改变第三方许可。"
            "只有经过实际发布的站点才可称为公网文档；本地构建不会上传或推送。\n",
            encoding="utf-8",
        )
        english_download = ""
        if archive_bytes is not None:
            english_download = (
                "## Download matching source\n\n[Download source ZIP](downloads/orgrebase-source.zip) · "
                "[SHA-256 checksums](downloads/SHA256SUMS.txt)\n\n"
                f"SHA-256: `{args.source_sha256}`. Size: {len(archive_bytes):,} bytes.\n\n"
                "Document, referenced-source and site-tooling bytes were compared against this archive. A download is not customer or production qualification.\n\n"
            )
        (pages / "source-version.en.md").write_text(
            "# Site source and downloads\n\n"
            + english_download
            + "This site is generated from the same source tree's Markdown guides, repository documentation and license files. Core guides are paired in Chinese and English; detailed original-language references are labeled.\n\n"
            f"Content identity for documents, referenced source and site tooling: `{digest}`. This is not a whole-product source digest or release qualification.\n\n"
            "[Per-file source manifest](source-manifest.json) records the bytes used. Local source-reference pages never substitute a different remote main revision. Directories, large files and missing historical assets are explicitly distinguished.\n\n"
            "Repository main, published releases and this site snapshot may differ. Reproduce with the matching source ZIP and checksum, or the same exact source revision. The distribution and site source download should use the same ZIP and digest.\n\n"
            "OrgRebase project-owned material uses the same Apache-2.0 and CC BY 4.0 path licenses as OAC. A local site build does not change third-party licenses and does not push or publish a public website.\n",
            encoding="utf-8",
        )
        config = yaml.safe_load((ROOT / "documentation/mkdocs.yml").read_text())
        config["theme"]["custom_dir"] = str(ROOT / "documentation/overrides")
        for extension in config["markdown_extensions"]:
            if isinstance(extension, dict) and "toc" in extension:
                extension["toc"]["slugify"] = slugify_unicode
        config.update(
            docs_dir=str(pages),
            site_dir=str(output),
            copyright=f"OrgRebase · Source-available · Documentation snapshot {digest[:12]}",
        )
        if args.site_url:
            config["site_url"] = args.site_url
        configuration = work / "mkdocs.yml"
        configuration.write_text(yaml.dump(config, allow_unicode=True, sort_keys=False))
        subprocess.run(
            [sys.executable, "-m", "mkdocs", "build", "--strict", "--config-file", str(configuration)],
            check=True,
        )
    print(
        json.dumps(
            {
                "status": "PASS",
                "site": str(output),
                "content_sha256": digest,
                "document_count": len(mapping),
                "source_reference_count": len(references),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
