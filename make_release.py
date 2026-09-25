"""准备一次公开发布：同步插件文件 → 抹掉配置页里的现网默认值 → 扫残留 → 打平铺 zip。

用法（在仓库根执行）:
    python make_release.py [--src ../doors_bot]

需要先有本地文件 `local_ids.txt`（已 gitignore，不进仓库、不上传）：
每行一个"只属于现网"的字符串（群号、机器人QQ、管理员QQ 等），脚本会扫描全部
待发布文件并阻止它们出现在公开仓库里。

产出:
    doors_bot/                      与 --src 一致，但配置页默认值改为占位
    release/doors_bot_v<版本>.zip   平铺插件包（Release 附件 / 分发件），桌面同步一份
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import sys
import zipfile

REPO = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.join(REPO, "doors_bot")
IDS_FILE = os.path.join(REPO, "local_ids.txt")
SKIP = {"__pycache__", ".env", "config", "config.zip", "deploy.sh"}
# 配置页默认值属于现网信息，发布版一律留空由用户自己填
BLANK_DEFAULTS = (("group_id", "default"), ("no_score_qqs", "default"))


def read_version() -> str:
    with open(os.path.join(PLUGIN, "metadata.yaml"), encoding="utf-8") as f:
        for line in f:
            m = re.match(r'\s*version:\s*"?v?([\w.]+)"?', line)
            if m:
                return m.group(1)
    sys.exit("metadata.yaml 里没找到 version")


def read_ids() -> list[str]:
    if not os.path.isfile(IDS_FILE):
        sys.exit(f"缺少 {os.path.basename(IDS_FILE)}：先写进群号/QQ 等现网标识，一行一个")
    with open(IDS_FILE, encoding="utf-8") as f:
        return [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]


def sync(src: str) -> int:
    n = 0
    for name in sorted(os.listdir(src)):
        if name in SKIP or os.path.isdir(os.path.join(src, name)) or name.startswith("doors_bot_v"):
            continue
        shutil.copy2(os.path.join(src, name), os.path.join(PLUGIN, name))
        n += 1
    return n


def blank_defaults() -> None:
    path = os.path.join(PLUGIN, "_conf_schema.json")
    # newline="" 保住原有 CRLF，否则 diff 会变成"整个文件都不同"
    with open(path, encoding="utf-8", newline="") as f:
        text = f.read()
    for key, field in BLANK_DEFAULTS:
        pat = re.compile(
            r'("%s"\s*:\s*\{(?:[^{}]|\n)*?)("%s"\s*:\s*)"[^"]*"' % (key, field), re.S
        )
        new, cnt = pat.subn(lambda m: m.group(1) + m.group(2) + '""', text, 1)
        if cnt != 1:
            sys.exit(f"配置页默认值没替换成功: {key}.{field}（schema 结构变了，改脚本）")
        text = new
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def scan(ids: list[str]) -> list[str]:
    pat = re.compile("|".join(re.escape(v) for v in ids))
    hits = []
    for root, dirs, files in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in {".git", "__pycache__", "release"}]
        for name in files:
            p = os.path.join(root, name)
            if p == IDS_FILE or name.endswith((".png", ".zip", ".db")):
                continue
            try:
                with open(p, encoding="utf-8") as f:
                    body = f.read()
            except (UnicodeDecodeError, OSError):
                continue
            for ln, line in enumerate(body.splitlines(), 1):
                if pat.search(line):
                    rel = os.path.relpath(p, REPO)
                    hits.append(f"{rel}:{ln}: {line.strip()[:70]}")
    return hits


def build_zip(version: str) -> str:
    out = os.path.join(REPO, "release", f"doors_bot_v{version}.zip")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    names = sorted(
        f for f in os.listdir(PLUGIN)
        if os.path.isfile(os.path.join(PLUGIN, f)) and f not in SKIP
    )
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in names:
            z.write(os.path.join(PLUGIN, f), f)
    desk = os.path.expanduser(f"~/Desktop/doors_bot_v{version}.zip")
    shutil.copy2(out, desk)
    with open(out, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    print(f"包: {out}\n    {len(names)} 条目 / {os.path.getsize(out)} 字节 / sha256 {digest[:12]}")
    print(f"桌面: {desk}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=os.path.join(os.path.dirname(REPO), "doors_bot"))
    args = ap.parse_args()
    if not os.path.isfile(os.path.join(args.src, "main.py")):
        sys.exit(f"--src 不像插件目录: {args.src}")
    ids = read_ids()
    print(f"同步 {args.src} -> {PLUGIN}: {sync(args.src)} 个文件")
    blank_defaults()
    print("配置页默认值已留空（group_id / no_score_qqs）")
    hits = scan(ids)
    if hits:
        print("扫到现网信息，发布中止：")
        for h in hits:
            print("  " + h)
        sys.exit(1)
    print("全仓扫描: 没有现网群号/QQ 残留")
    build_zip(read_version())


if __name__ == "__main__":
    main()
