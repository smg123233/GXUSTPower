"""为 MSI 生成组件清单（build/files.wxs）。

输入：PyInstaller onedir 产物目录（默认 dist\\DormPower）
输出：WiX 片段，包含安装目录树、每个文件的 Component、以及 ComponentGroup AppFiles。
组件 GUID 用 uuid5 由相对路径确定性生成，重复构建不会变化。
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "dist" / "GXUSTPower"
OUTPUT = ROOT / "build" / "files.wxs"

NS = uuid.UUID("6f1e2c1a-9d1b-4b57-9c3e-2a4d5b6c7e8f")
APP_NS = uuid.UUID("0b9c8d7e-6f5a-4b3c-8d2e-1f0a9b8c7d6e")


def file_guid(relative: str) -> str:
    return "{" + str(uuid.uuid5(NS, "DormPower/file/" + relative)).upper() + "}"


def component_guid(relative: str) -> str:
    return "{" + str(uuid.uuid5(NS, "DormPower/component/" + relative)).upper() + "}"


def file_id(relative: str) -> str:
    return "fil_" + uuid.uuid5(APP_NS, relative).hex[:16]


def component_id(relative: str) -> str:
    return "cmp_" + uuid.uuid5(APP_NS, relative).hex[:16]


def dir_id(relative: str) -> str:
    return "dir_" + uuid.uuid5(APP_NS, relative).hex[:16]


def collect() -> list[Path]:
    if not SOURCE.is_dir():
        raise SystemExit(f"找不到目录：{SOURCE}")
    return sorted(p for p in SOURCE.rglob("*") if p.is_file())


def build_tree(files: list[Path]) -> dict:
    tree: dict = {}
    for path in files:
        rel = path.relative_to(SOURCE)
        parts = rel.parts[:-1]
        node = tree
        for part in parts:
            node = node.setdefault(part, {})
        node.setdefault("__files__", []).append(rel.name)
    return tree


def emit_tree(node: dict, prefix: str, indent: int, out: list[str]) -> None:
    pad = "  " * indent
    for name in sorted(k for k in node if k != "__files__"):
        rel = f"{prefix}/{name}" if prefix else name
        out.append(f'{pad}<Directory Id="{dir_id(rel)}" Name={quoteattr(name)}>')
        emit_tree(node[name], rel, indent + 1, out)
        out.append(f"{pad}</Directory>")
    for rel_name in sorted(node.get("__files__", [])):
        rel = f"{prefix}/{rel_name}" if prefix else str(rel_name)
        source = "$(var.SourceDir)\\" + str(rel).replace("/", "\\")
        out.append(f'{pad}<Component Id="{component_id(rel)}" Guid="{component_guid(rel)}" Win64="yes">')
        out.append(f'{pad}  <File Id="{file_id(rel)}" Source="{escape(source)}" KeyPath="yes" />')
        out.append(f"{pad}</Component>")


def main() -> None:
    files = collect()
    if not files:
        raise SystemExit("没有找到任何文件，先运行 PyInstaller")
    tree = build_tree(files)
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<Wix xmlns="http://schemas.microsoft.com/wix/2006/wi">',
        "  <Fragment>",
        '    <DirectoryRef Id="INSTALLFOLDER">',
    ]
    emit_tree(tree, "", 3, lines)
    lines.append("    </DirectoryRef>")
    lines.append('    <ComponentGroup Id="AppFiles">')
    for path in files:
        rel = "/".join(path.relative_to(SOURCE).parts)
        lines.append(f'      <ComponentRef Id="{component_id(rel)}" />')
    lines.append("    </ComponentGroup>")
    lines.append("  </Fragment>")
    lines.append("</Wix>")

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"written {OUTPUT} ({len(files)} files)")


if __name__ == "__main__":
    main()
