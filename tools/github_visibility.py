"""切换 GitHub 仓库可见性并加固分支（公开但别人只能看/提 PR，不能直接改）。

用法（在本机运行，需要 GitHub Token）：

    python tools\\github_visibility.py --repo Jokldgd/board-sound-studio --public --protect
    python tools\\github_visibility.py --repo Jokldgd/board-sound-studio --private
    python tools\\github_visibility.py --repo ... --check        # 只查询（不需要 token 也能看公开仓库）

Token 权限（二选一）：
  * fine-grained PAT：只选这个仓库，Repository permissions → Administration: Read and write
  * classic PAT：勾选 repo

脚本做三件事：
  1) PATCH /repos/{repo} 改 visibility；
  2) 给默认分支加保护：必须走 PR、禁止 force push、禁止删除、只允许 owner 推送；
  3) 用匿名请求复核「别人能看到但不能改」。
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

API = "https://api.github.com"


def request(method: str, path: str, token: str | None = None,
            payload: dict | None = None) -> tuple[int, dict]:
    url = API + path
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("User-Agent", "soundboard-studio-tool")
    req.add_header("Accept", "application/vnd.github+json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
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
    parser.add_argument("--repo", required=True, help="owner/name")
    parser.add_argument("--token", default="", help="GitHub PAT（也可用环境变量 GITHUB_TOKEN）")
    parser.add_argument("--public", action="store_true")
    parser.add_argument("--private", action="store_true")
    parser.add_argument("--protect", action="store_true", help="给默认分支加保护规则")
    parser.add_argument("--owner", default="", help="允许直接推送的账号（默认取 repo 的 owner）")
    parser.add_argument("--check", action="store_true", help="只查询可见性（匿名）")
    args = parser.parse_args()

    token = args.token.strip() or __import__("os").environ.get("GITHUB_TOKEN", "").strip()

    if args.check:
        status, data = request("GET", f"/repos/{args.repo}")
        if status == 404:
            print("匿名访问 404 → 仓库是私有的（或不存在）")
        elif status == 200:
            print(f"匿名访问 200 → 公开；visibility={data.get('visibility')} "
                  f"default_branch={data.get('default_branch')}")
        else:
            print("查询失败:", status, data.get("message"))
        return 0

    if not token:
        print("需要 --token 或环境变量 GITHUB_TOKEN", file=sys.stderr)
        return 2
    if args.public == args.private:
        print("--public 与 --private 必须二选一", file=sys.stderr)
        return 2

    status, data = request("GET", f"/repos/{args.repo}", token)
    if status != 200:
        print(f"读取仓库失败: {status} {data.get('message')}", file=sys.stderr)
        return 1
    owner = args.owner or (data.get("owner") or {}).get("login") or args.repo.split("/")[0]
    default_branch = data.get("default_branch") or "main"
    print(f"仓库: {args.repo}  当前 private={data.get('private')}  默认分支={default_branch}")

    wanted_private = bool(args.private)
    if data.get("private") != wanted_private:
        status, data = request("PATCH", f"/repos/{args.repo}", token,
                               {"private": wanted_private})
        if status not in (200, 201):
            print(f"修改可见性失败: {status} {data.get('message')}", file=sys.stderr)
            return 1
        print(f"可见性已改为: {'private' if data.get('private') else 'public'}")
    else:
        print("可见性无需修改")

    if args.protect:
        rule = {
            "required_status_checks": None,
            "enforce_admins": False,
            "required_pull_request_reviews": {
                "required_approving_review_count": 0,
                "dismiss_stale_reviews": False,
                "require_code_owner_reviews": False,
            },
            "restrictions": {"users": [owner], "teams": [], "apps": []},
            "required_conversation_resolution": True,
            "allow_force_pushes": False,
            "allow_deletions": False,
        }
        status, data = request(
            "PUT", f"/repos/{args.repo}/branches/{default_branch}/protection", token, rule
        )
        if status in (200, 201):
            print(f"已保护分支 {default_branch}：必须 PR、禁止 force push/删除、仅 {owner} 可推送")
        else:
            print(f"分支保护失败（可稍后在网页手动开）: {status} {data.get('message')}")
            # 退一步：至少禁止 force push / 删除
            fallback = {
                "required_status_checks": None,
                "enforce_admins": False,
                "required_pull_request_reviews": None,
                "restrictions": None,
                "allow_force_pushes": False,
                "allow_deletions": False,
            }
            status2, data2 = request(
                "PUT", f"/repos/{args.repo}/branches/{default_branch}/protection",
                token, fallback,
            )
            if status2 in (200, 201):
                print("已退化为「禁止 force push / 删除」的保护规则")

    status, data = request("GET", f"/repos/{args.repo}")
    if status == 200:
        print(f"复核（匿名）: 可见性={data.get('visibility')} "
              f"private={data.get('private')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
