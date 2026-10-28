"""解压 VB-CABLE 驱动包（**必须整包解压**，不能只拿 exe）。

为什么：`VBCABLE_Setup_x64.exe` 不是自包含安装器，它安装时需要同目录下的
`vbMmeCable64_win7.inf` / `vbaudio_cable64_win7.sys` / `.cat` 等文件。
只复制 exe 会报 "Missing 'inf' file or Driver package corrupted..."。

用法：
    python packaging/extract_cable.py <zip> <输出目录>
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: extract_cable.py <zip> <outdir>")
        return 2
    archive = Path(sys.argv[1])
    outdir = Path(sys.argv[2])
    outdir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(archive) as zf:
        names = zf.namelist()
        zf.extractall(outdir)
    setup = outdir / "VBCABLE_Setup_x64.exe"
    if not setup.is_file():
        print(f"VBCABLE_Setup_x64.exe not found in {archive.name}")
        return 1
    print(f"extracted {len(names)} files -> {outdir} (setup: {setup.name})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
