"""
doors_bot - 本地命令行查榜工具（不经过 AstrBot，直接读 doors.db，只读不改数据）

用法（在插件目录内，或任意目录）:
    python -m doors_bot.cli                  # 等同 all
    python data/plugins/doors_bot/cli.py all

子命令:
    all                    依次输出 今日/昨日/本月/上月 四张榜
    today                  今日实时榜（00:00 ~ 当前）
    yesterday              昨日日榜
    day   [日期]            指定日期日榜，如 day 2026-09-24 / day 昨天
    month [月份]            指定月份月榜，如 month 2026-08 / month 上月
    lastmonth              上月结算榜（含应发 Robux）
    diag                   自检：库路径、各表行数、漏算日期、任务状态

可选参数:
    --db PATH   临时指定数据库文件
                默认按 --db > WebUI插件配置db_path > 插件外数据目录doors.db 定位
"""

from __future__ import annotations

import argparse
import datetime
import os
import sys

_PKG_DIR = os.path.dirname(os.path.abspath(__file__))
_PARENT_DIR = os.path.dirname(_PKG_DIR)
for _p in (_PARENT_DIR, _PKG_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

if __package__ in (None, ""):
    # python cli.py 直接运行时按包名导入，复用包内相对导入
    import importlib

    _pkg = os.path.basename(_PKG_DIR)
    admin_ops = importlib.import_module(f"{_pkg}.admin_ops")
    report = importlib.import_module(f"{_pkg}.report")
    runtime_config = importlib.import_module(f"{_pkg}.runtime_config")
else:
    from . import admin_ops, report, runtime_config


def _print(text: str) -> None:
    # 按控制台编码转一遍：昵称里自带的 emoji 在 GBK 码页会变成 ? 而不是崩。
    enc = getattr(sys.stdout, "encoding", None) or "utf-8"
    print(text.encode(enc, errors="replace").decode(enc, errors="replace"))


def _load_runtime_config() -> dict:
    """读取 AstrBot 写给插件的落盘配置(data/config/doors_bot_config.json)。

    插件运行时的 group_id/db_path 来自 WebUI 配置页而非 .env，
    CLI 必须按同一份配置取库，否则两边看到的不是同一个数据库。
    """
    for rel in (
        os.path.join(_PARENT_DIR, "..", "config", "doors_bot_config.json"),
        os.path.join(_PARENT_DIR, "config", "doors_bot_config.json"),
        os.path.join(_PKG_DIR, "doors_bot_config.json"),
    ):
        path = os.path.abspath(rel)
        if not os.path.exists(path):
            continue
        try:
            import json

            with open(path, encoding="utf-8-sig") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception as e:
            print(f"[doors_bot.cli] 读取配置失败 {path}: {e}")
    return {}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="doors_bot.cli", description="doors_bot 本地查榜（只读，不会修改数据）"
    )
    parser.add_argument(
        "board",
        nargs="?",
        default="all",
        choices=["all", "today", "yesterday", "day", "month", "lastmonth", "diag"],
        help="要查看的榜单/自检，默认 all",
    )
    parser.add_argument(
        "value",
        nargs="?",
        default="",
        help="day 接日期(2026-09-24/昨天)，month 接月份(2026-08/上月)，支持中文写法",
    )
    parser.add_argument("--db", help="数据库文件路径（覆盖插件配置与默认数据目录）")
    args = parser.parse_args(argv)

    conf = _load_runtime_config()
    if conf:
        runtime_config.set_plugin_config(conf)

    if args.db:
        db = os.path.abspath(args.db)
        if not os.path.exists(db):
            print(f"[doors_bot.cli] 数据库不存在: {db}")
            return 1
        runtime_config._config["db_path"] = db

    db_path = runtime_config.get_db_path()
    if not os.path.exists(db_path):
        print(f"[doors_bot.cli] 数据库不存在: {db_path}")
        print("提示: 用 --db 指定，或在 AstrBot 插件配置页填写 db_path。")
        return 1

    now = datetime.datetime.now()
    today = now.date()
    boards: list[tuple[str, object]] = []
    if args.board == "all":
        boards = [
            ("今日实时榜", report.render_today_board),
            ("昨日日榜", report.render_yesterday_board),
            ("本月实时预估", report.render_current_month_board),
            ("上月结算榜", report.render_last_month_board),
        ]
    elif args.board == "day":
        day = admin_ops.parse_date(args.value, today)
        if not day:
            print(f"[doors_bot.cli] 无法识别日期: {args.value!r}，例: day 2026-09-24 / day 昨天")
            return 1
        boards = [(f"发言日榜 {day}", lambda n, d=day: report.render_day_board(d, n))]
    elif args.board == "month":
        month = admin_ops.parse_month(args.value, today)
        if not month:
            print(
                f"[doors_bot.cli] 无法识别月份: {args.value!r}，例: month 2026-08 / month 上月"
            )
            return 1
        boards = [(f"发言月榜 {month}", lambda n, m=month: report.render_month_board(m, n))]
    elif args.board == "diag":
        boards = [("自检", report.render_diag)]
    else:
        single = {
            "today": ("今日实时榜", report.render_today_board),
            "yesterday": ("昨日日榜", report.render_yesterday_board),
            "lastmonth": ("上月结算榜", report.render_last_month_board),
        }
        title, fn = single[args.board]
        boards = [(title, fn)]

    for i, (title, fn) in enumerate(boards):
        if i:
            print()
        _print(f"===== {title} @ {now:%Y-%m-%d %H:%M} (db: {db_path}) =====")
        try:
            _print(fn(now))
        except Exception as e:
            _print(f"[doors_bot.cli] 渲染 {title} 失败: {e}")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
