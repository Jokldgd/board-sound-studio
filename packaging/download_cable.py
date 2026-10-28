"""下载 VB-CABLE 官方安装包（PowerShell 的 Invoke-WebRequest 在部分环境 TLS 失败，
用 Python 的 urllib 更稳）。

用法：
    python packaging/download_cable.py <输出zip路径> [url]
"""

from __future__ import annotations

import sys
import urllib.request

DEFAULT_URL = "https://download.vb-audio.com/Download_CABLE/VBCABLE_Driver_Pack43.zip"


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: download_cable.py <out.zip> [url]")
        return 2
    target = sys.argv[1]
    url = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_URL
    request = urllib.request.Request(url, headers={"User-Agent": "soundboard-studio-installer"})
    with urllib.request.urlopen(request, timeout=120) as response, open(target, "wb") as handle:
        data = response.read()
        handle.write(data)
    print(f"downloaded {len(data)} bytes -> {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
