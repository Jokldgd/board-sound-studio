"""解压 VB-CABLE 安装包，取出 VBCABLE_Setup_x64.exe（不依赖 Expand-Archive）。

为什么不用 PowerShell 的 Expand-Archive：在无交互/重定向输出的环境里它会调用
Write-Progress，某些主机会抛 "Access is denied ... console output buffer"。
zipfile 没有这个问题。

用法：
    python packaging/extract_cable.py <zip> <输出目录> [目标文件名]
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: extract_cable.py <zip> <outdir> [name]")
        return 2
    archive = Path(sys.argv[1])
    outdir = Path(sys.argv[2])
    target = sys.argv[3] if len(sys.argv) > 3 else "VBCABLE_Setup_x64.exe"
    outdir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(archive) as zf:
        hits = [n for n in zf.namelist() if Path(n).name.lower() == target.lower()]
        if not hits:
            print(f"{target} not found in {archive.name}")
            return 1
        member = hits[0]
        data = zf.read(member)
        destination = outdir / target
        destination.write_bytes(data)
    print(f"extracted {member} -> {destination} ({len(data)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
