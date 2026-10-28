"""创建 GitHub Release 并上传安装包（需要 token）。

用法：

    python tools\\upload_release.py --repo Jokldgd/board-sound-studio ^
        --tag v1.3.3 --name "Soundboard Studio 1.3.3" ^
        --notes-file packaging\\RELEASE_NOTES-1.3.3.md ^
        --asset installer\\release\\SoundboardStudio-Setup-1.3.3.exe ^
        --asset packaging\\extra\\安装说明.txt

Token 权限：
  * classic PAT：勾选 `repo`（public_repo 不够，要能写 Releases）
  * fine-grained：Contents: Read and write（+ Administration 可选）

脚本行为：
  1) 若该 tag 的 Release 不存在则创建，存在则复用；
  2) 同名资产已存在时先删除再上传（可重复执行）；
  3) 上传后校验 size 与本地一致，并打印 Release 页面地址。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://api.github.com"
UPLOAD = "https://uploads.github.com"


def _req(method: str, url: str, token: str, payload: dict | None = None,
         raw: bytes | None = None, content_type: str = "application/json",
         timeout: int = 300):
    data = raw if raw is not None else (
        json.dumps(payload).encode("utf-8") if payload is not None else None
    )
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("User-Agent", "soundboard-studio-uploader")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        req.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8") or "{}"
            return resp.status, json.loads(body)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            parsed = json.loads(body)
        except ValueError:
            parsed = {"message": body[:300]}
        return exc.code, parsed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--name", default="")
    parser.add_argument("--notes-file", default="")
    parser.add_argument("--asset", action="append", default=[])
    parser.add_argument("--token", default="")
    parser.add_argument("--draft", action="store_true")
    parser.add_argument("--prerelease", action="store_true")
    args = parser.parse_args()

    token = args.token.strip() or os.environ.get("GH_TOKEN", "").strip()
    if not token:
        print("需要 --token 或 GH_TOKEN", file=sys.stderr)
        return 2

    name = args.name or f"Soundboard Studio {args.tag.lstrip('v')}"
    body = ""
    if args.notes_file:
        body = Path(args.notes_file).read_text(encoding="utf-8")

    payload = {
        "tag_name": args.tag,
        "name": name,
        "body": body,
        "draft": bool(args.draft),
        "prerelease": bool(args.prerelease),
    }
    status, release = _req("POST", f"{API}/repos/{args.repo}/releases", token, payload)
    if status == 422:  # 已存在
        status, release = _req("GET", f"{API}/repos/{args.repo}/releases/tags/{args.tag}", token)
    if status not in (200, 201):
        print(f"创建 Release 失败: {status} {release.get('message')}", file=sys.stderr)
        return 1
    release_id = release.get("id")
    html_url = release.get("html_url")
    print(f"Release: {html_url}")

    for path_str in args.asset:
        path = Path(path_str)
        if not path.is_file():
            print(f"跳过（文件不存在）: {path}", file=sys.stderr)
            continue
        size = path.stat().st_size
        existing_status, assets = _req(
            "GET", f"{API}/repos/{args.repo}/releases/{release_id}/assets", token
        )
        if existing_status == 200:
            for asset in assets:
                if asset.get("name") == path.name:
                    s, _ = _req(
                        "DELETE",
                        f"{API}/repos/{args.repo}/releases/assets/{asset['id']}",
                        token,
                    )
                    print(f"删除旧资产 {path.name}: {s}")
        print(f"上传 {path.name}（{size / 1024 / 1024:.1f} MB）…")
        data = path.read_bytes()
        status, asset = _req(
            "POST",
            f"{UPLOAD}/repos/{args.repo}/releases/{release_id}/assets?name={urllib.parse.quote(path.name)}",
            token,
            raw=data,
            content_type="application/octet-stream",
        )
        if status not in (200, 201):
            print(f"上传失败: {status} {asset.get('message')}", file=sys.stderr)
            return 1
        remote = asset.get("size")
        print(f"  OK size={remote}（本地 {size}） state={asset.get('state')}")
        if remote != size:
            print("  警告：远端大小与本地不一致！", file=sys.stderr)

    status, release = _req("GET", f"{API}/repos/{args.repo}/releases/tags/{args.tag}", token)
    if status == 200:
        print(f"完成：{release.get('html_url')}  资产数={len(release.get('assets') or [])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
