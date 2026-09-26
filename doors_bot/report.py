"""
doors_bot - 后台榜单渲染（仅 AstrBot WebUI 聊天窗 / 本地 cli.py 使用）

- 群聊/私聊平台(aiocqhttp 等)不会触发这些指令，机器人在 QQ 里保持绝对静默
- 输出为无 emoji 的等宽对齐表格；ChatUI 侧用 fenced=True 包一层代码块保证对齐
- 后台属于管理端，输出包含 Robux 列（已结算月为落库值，未结算月为实时预估）
- 本模块只读库，任何改写/删除数据的操作在 admin_ops.py
"""

from __future__ import annotations

import datetime
import os
import sqlite3
import unicodedata

from .daily_score import valid_msg_counts
from .monthly_settle import (
    adjust_totals,
    compute_awards,
    consecutive_award_count,
    get_month_range,
    is_newbie,
    prev_month,
)
from .runtime_config import (
    estimate_day_score,
    get_daily_config,
    get_data_dir,
    get_db_path,
    get_group_id,
    get_monthly_config,
    get_no_score_qqs,
    get_report_config,
    get_scheduler_config,
    legacy_db_candidates,
)

NAME_WIDTH = 32  # 用户列固定宽度 = QQ 昵称的最大显示宽度(中文按 2 列计)，短的名词也补满
COL_RANK, COL_QQ, COL_SCORE, COL_COUNT, COL_MONEY = 6, 12, 10, 8, 12
# 名次/用户/QQ/总分/Robux 全部定宽；日榜的"备注"列排在最后，不占列宽也不补齐
MONTH_WIDTHS = (COL_RANK, NAME_WIDTH, COL_QQ, COL_SCORE, COL_MONEY)
DAY_WIDTHS = MONTH_WIDTHS[:4] + (COL_COUNT,)
_INVISIBLE_CATEGORIES = ("Mn", "Me", "Cf", "Cc", "Cs", "Co", "Cn")
# 韩文填充符、不间断空格这类"看起来是空白"的字符宽度随字体浮动，一律换成真空格
_BLANKISH = {0x115F, 0x1160, 0x3164, 0xFFA0, 0x00A0, 0x2000, 0x2007, 0x202F}


def _clean(text: str) -> str:
    """清掉会让浏览器排版错位的字符：双向控制符、零宽符、控制字符、假空格。

    QQ 昵称里常见 U+202D/U+2067(双向隔离)与 U+1160(韩文填充符)，它们参与渲染
    却不参与宽度计算，不去掉的话后面每一列都会跟着偏。
    """
    out = []
    for ch in str(text or ""):
        if ord(ch) in _BLANKISH:
            out.append(" ")
        elif unicodedata.category(ch) in _INVISIBLE_CATEGORIES:
            continue
        else:
            out.append(ch)
    return " ".join("".join(out).split())


def _width(text: str) -> int:
    """显示宽度：汉字/全角/表情按 2 列，隐形字符按 0 列，其余按 1 列。

    East-Asian-Ambiguous(• ʞ ± 等)按 1 列：后台代码块用的是拉丁等宽字体，
    这些字符由它自己渲染成半角，只有汉字才回退到中文字体占 2 列。
    """
    n = 0
    for ch in text:
        if unicodedata.category(ch) in _INVISIBLE_CATEGORIES:
            continue
        n += 2 if unicodedata.east_asian_width(ch) in ("F", "W") else 1
    return n


def _clip(text: str, max_w: int) -> str:
    if _width(text) <= max_w:
        return text
    out, used = "", 0
    for ch in text:
        w = _width(ch)
        if used + w > max_w - 1:
            break
        out += ch
        used += w
    return out + "."


def _pad(text: str, width: int, align: str = "left") -> str:
    gap = max(0, width - _width(text))
    return text + " " * gap if align in ("l", "left") else " " * gap + text


def _table(
    headers: list[str],
    rows: list[list[str]],
    aligns: str = "",
    widths: tuple[int, ...] = MONTH_WIDTHS,
) -> list[str]:
    """定宽表格：aligns 每列 'l' 或 'r'，widths 是前 N 列的固定宽度。

    每行第 i 列都从同一个字符位置开始，与这一行的内容长短无关；超出列宽的内容
    直接截断。只有最后一列(句数/备注/Robux)不补齐，因为它后面没有别的列了。
    """
    aligns = (aligns + "l" * len(headers))[: len(headers)]

    def line(cells: list[str]) -> str:
        parts = [
            _pad(_clip(c, widths[i]), widths[i], aligns[i]) if i < len(widths) else c
            for i, c in enumerate(cells)
        ]
        return "  ".join(parts).rstrip()

    return [line(headers)] + [line(r) for r in rows]


def _fence(text: str, fenced: bool) -> str:
    return f"```\n{text}\n```" if fenced else text


def _fmt_score(v: float) -> str:
    return f"{round(float(v), 2):g}"


def _day_cap(cfg: dict) -> float:
    """单日能拿到的满分 = 发言分上限 + 达量奖励。"""
    return round(float(cfg["daily_cap"]) + float(cfg["bonus_score"]), 2)


def _day_note(score: float, cfg: dict) -> str:
    """达上限的说明写在最后一列：它后面没有列，写多长都不会挤歪表格。"""
    return "（已抵达今日上限）" if score >= _day_cap(cfg) - 1e-9 else ""


def _names(c: sqlite3.Cursor) -> dict[int, str]:
    return {
        int(qq): (_clean(name) or str(qq))
        for qq, name in c.execute("SELECT qq, name FROM user_info")
    }


def _limit() -> int:
    """<=0 表示不限量（榜单完整显示）。"""
    return int(get_report_config()["display_limit"])


def _apply_limit(rows: list, limit: int, score_at: int = -1) -> tuple[list, int]:
    """返回 (显示行, 被省略行数)。0 分的行一律不显示。"""
    rows = [r for r in rows if abs(float(r[score_at] or 0)) >= 0.005]
    if limit <= 0 or len(rows) <= limit:
        return rows, 0
    return rows[:limit], len(rows) - limit


def _tie_ranks(values: list[float]) -> list[int]:
    """同分并列名次（1,2,2,4 式）。values 需已按分数降序。"""
    ranks: list[int] = []
    prev: float | None = None
    last = 0
    for pos, v in enumerate(values, start=1):
        if prev is None or abs(float(v) - float(prev)) >= 0.005:
            prev, last = v, pos
        ranks.append(last)
    return ranks


def _day_order(row: tuple[int, int, float]):
    """日榜排序：分数降序 → 同分按句数降序 → 再同按 QQ 升序。

    并列名次的先后不影响奖金，但句数多的那条要排在前面。
    """
    qq, sentences, score = row
    return (-float(score), -int(sentences), int(qq))


def _board_row(rank: int, qq: int, names: dict[int, str], score_col: str, *extra) -> list[str]:
    return [str(rank), names.get(qq, str(qq)), str(qq), score_col, *[str(x) for x in extra]]


def _month_settled(c: sqlite3.Cursor, month: str) -> bool:
    return bool(
        c.execute(
            "SELECT 1 FROM month_score WHERE month=? AND settled=1 LIMIT 1", (month,)
        ).fetchone()
    )


def _live_day_scores(conn: sqlite3.Connection, dates: list[str]) -> dict[int, float]:
    """按 msg 实时补算指定日期的发言分（不写库）。

    23:59 任务漏跑、或今天还没结算时，月榜靠它把分数补齐，避免"月榜没有昨天的数据"。
    """
    cfg = get_daily_config()
    group_id = get_group_id()
    out: dict[int, float] = {}
    for d in dates:
        for qq, cnt in valid_msg_counts(
            conn, group_id, d, cfg["multi_speaker_window_seconds"],
            cfg["multi_speaker_min_count"],
        ).items():
            out[qq] = round(out.get(qq, 0.0) + estimate_day_score(cnt, cfg), 2)
    return out


def _missing_days(c: sqlite3.Cursor, month: str, until_date: str) -> list[str]:
    """该月有 msg 但没有 daily_score 的日期。"""
    with_msg = {
        r[0]
        for r in c.execute(
            "SELECT DISTINCT date FROM msg WHERE date LIKE ? AND date<=?",
            (f"{month}%", until_date),
        )
    }
    done = {
        r[0]
        for r in c.execute(
            "SELECT DISTINCT date FROM daily_score WHERE date LIKE ?", (f"{month}%",)
        )
    }
    return sorted(with_msg - done)


def render_day_board(
    date_str: str,
    now: datetime.datetime | None = None,
    fenced: bool = False,
) -> str:
    """指定日期发言榜。当天未过完时按 msg 实时计算；历史日优先用已落库日分。"""
    now = now or datetime.datetime.now()
    cfg = get_daily_config()
    group_id = get_group_id()
    is_today = date_str == now.strftime("%Y-%m-%d")
    conn = sqlite3.connect(get_db_path())
    try:
        c = conn.cursor()
        names = _names(c)
        rows: list[tuple[int, int, float]] = []  # (qq, sentences, score)
        if is_today:
            vc = valid_msg_counts(
                conn, group_id, date_str,
                cfg["multi_speaker_window_seconds"], cfg["multi_speaker_min_count"],
            )
            rows = [(qq, cnt, estimate_day_score(cnt, cfg)) for qq, cnt in vc.items()]
            title = f"发言日榜(今日实时) {date_str} 00:00 ~ {now:%H:%M}"
            note = (
                f"注: 未发言扣分(-{cfg['penalty']:g})于每日 "
                f"{get_scheduler_config()['daily_job_hour']:02d}:"
                f"{get_scheduler_config()['daily_job_minute']:02d} 结算时生效"
            )
        else:
            stored = c.execute(
                "SELECT qq, sentences, score FROM daily_score WHERE date=? "
                "ORDER BY score DESC",
                (date_str,),
            ).fetchall()
            if stored:
                rows = [(int(q), int(s or 0), float(v or 0)) for q, s, v in stored]
                note = ""
            else:
                vc = valid_msg_counts(
                    conn, group_id, date_str,
                    cfg["multi_speaker_window_seconds"],
                    cfg["multi_speaker_min_count"],
                )
                rows = [(qq, cnt, estimate_day_score(cnt, cfg))
                         for qq, cnt in vc.items()]
                note = f"注: {date_str} 尚无日分记录(定时任务未跑过)，按发言记录实时计算"
            title = f"发言日榜 {date_str} 00:00 ~ 23:59"
    finally:
        conn.close()

    rows.sort(key=_day_order)
    limit = _limit()
    shown, hidden = _apply_limit(rows, limit)
    if not shown:
        return _fence(f"{title}\n当日暂无有效发言", fenced)
    body = [title] + _table(
        ["名次", "用户", "QQ", "得分", "句数", "备注"],
        [
            _board_row(rank, qq, names, _fmt_score(score), cnt, _day_note(score, cfg))
            for rank, (qq, cnt, score) in
            zip(_tie_ranks([s for _, _, s in shown]), shown)
        ],
        aligns="rllrrl",
        widths=DAY_WIDTHS,
    )
    tail = f"... 另有 {hidden} 行未显示" if hidden else ""
    if tail:
        body.append(tail)
    if note:
        body.append(note)
    if any(score >= _day_cap(cfg) - 1e-9 for _, _, score in shown):
        body.append(
            f"注: 单日上限 {_day_cap(cfg):g} 分"
            f"（发言分上限 {cfg['daily_cap']:g} + 达量奖励 {cfg['bonus_score']:g}）"
        )
    return _fence("\n".join(body), fenced)


def _render_settled_month(
    conn: sqlite3.Connection, c: sqlite3.Cursor, month: str, names: dict[int, str],
    limit: int, fenced: bool,
) -> str:
    days = get_month_range(month)
    title = f"发言月榜 {days[0]} ~ {days[-1]} (已结算)"
    rows = c.execute(
        "SELECT qq, total, robux FROM month_score "
        "WHERE month=? AND settled=1 ORDER BY total DESC, qq",
        (month,),
    ).fetchall()
    shown, hidden = _apply_limit([[q, t, r] for q, t, r in rows], limit, score_at=1)
    body = [title] + _table(
        ["名次", "用户", "QQ", "总分", "应发Robux"],
        [
            _board_row(rank, int(q), names, _fmt_score(float(t)),
                       f"{_fmt_score(float(ru))}R" if ru else "-")
            for rank, (q, t, ru) in
            zip(_tie_ranks([float(t) for q, t, r in shown]), shown)
        ],
        aligns="rllrr",
    )
    if hidden:
        body.append(f"... 另有 {hidden} 行未显示")
    return _fence("\n".join(body), fenced)


def _render_live_month(
    conn: sqlite3.Connection, c: sqlite3.Cursor, month: str, names: dict[int, str],
    limit: int, now: datetime.datetime, fenced: bool,
) -> str:
    """未结算月份的实时预估榜：日分 + 漏算日实时补算 + 结转/邀请分，套系数。"""
    cfg_m = get_monthly_config()
    last_day = now.strftime("%Y-%m-%d")
    days = [d for d in get_month_range(month) if d <= last_day]
    if not days:
        return _fence(f"发言月榜 {month}\n该月尚未开始，无数据", fenced)
    daily: dict[int, float] = {}
    for qq, s in c.execute(
        "SELECT qq, SUM(COALESCE(score,0)) FROM daily_score "
        "WHERE date>=? AND date<=? GROUP BY qq",
        (days[0], days[-1]),
    ):
        daily[int(qq)] = float(s or 0)

    missing = _missing_days(c, month, last_day)
    if missing:
        for qq, s in _live_day_scores(conn, missing).items():
            daily[qq] = round(daily.get(qq, 0.0) + s, 2)

    credits: dict[int, tuple[float, float]] = {}
    for qq, carry, bonus in c.execute(
        "SELECT qq, COALESCE(carry,0), COALESCE(bonus,0) FROM month_score WHERE month=?",
        (month,),
    ):
        credits[int(qq)] = (float(carry), float(bonus))

    # /扣除 的手工账调整：与结算同一口径，实时榜不含它就与最终月榜对不上
    adjust = adjust_totals(c, month)

    finals: list[tuple[int, float]] = []
    for qq in set(daily) | set(credits) | set(adjust):
        base = max(0.0, round(daily.get(qq, 0.0), 2))
        if is_newbie(qq, month, c):
            base = round(base * cfg_m["newbie_multiplier"], 2)
        n = consecutive_award_count(qq, month, c)
        if n > 0:
            base = round(base * max(0.0, 1 - cfg_m["decay_rate"] * n), 2)
        carry, bonus = credits.get(qq, (0.0, 0.0))
        finals.append(
            (qq, max(0.0, round(base + carry + bonus + adjust.get(qq, 0.0), 2)))
        )

    finals.sort(key=lambda x: (-x[1], x[0]))  # 同分按 QQ 升序，保证每次输出顺序一致
    top_n = int(cfg_m["top_n"])
    per = int(cfg_m["award_per_top"])
    title = f"发言月榜(实时预估) {days[0]} ~ {days[-1]}"
    notes = [f"注: 最终排名与 Robux 以 {get_scheduler_config()['monthly_job_day']}日结算为准"]
    if missing:
        notes.append(
            f"含 {len(missing)} 个未结算日按发言记录实时补算: "
            + ", ".join(missing[:10])
            + (" ..." if len(missing) > 10 else "")
        )
    shown, hidden = _apply_limit([[q, t] for q, t in finals], limit)
    if not shown:
        return _fence("\n".join([title, *notes, "本月暂无积分记录"]), fenced)
    # 奖金按结算同一口径（并列第 top_n 名平分那一份），预估榜才不会多报
    awards = compute_awards(finals, top_n, per)
    body = [title] + _table(
        ["名次", "用户", "QQ", "预估总分", "奖金"],
        [
            _board_row(rank, qq, names, _fmt_score(total),
                       f"{awards.get(qq, 0.0):g}R" if awards.get(qq) else "-")
            for rank, (qq, total) in
            zip(_tie_ranks([t for q, t in shown]), shown)
        ],
        aligns="rllrr",
    )
    if hidden:
        body.append(f"... 另有 {hidden} 行未显示")
    body += notes
    return _fence("\n".join(body), fenced)


def render_month_board(
    month: str, now: datetime.datetime | None = None, fenced: bool = False
) -> str:
    """指定月份(YYYY-MM)榜单：已结算读库，未结算实时预估。"""
    now = now or datetime.datetime.now()
    conn = sqlite3.connect(get_db_path())
    try:
        c = conn.cursor()
        names = _names(c)
        limit = _limit()
        if _month_settled(c, month):
            return _render_settled_month(conn, c, month, names, limit, fenced)
        return _render_live_month(conn, c, month, names, limit, now, fenced)
    finally:
        conn.close()


def render_current_month_board(
    now: datetime.datetime | None = None, fenced: bool = False
) -> str:
    now = now or datetime.datetime.now()
    return render_month_board(now.strftime("%Y-%m"), now=now, fenced=fenced)


def render_last_month_board(
    now: datetime.datetime | None = None, fenced: bool = False
) -> str:
    now = now or datetime.datetime.now()
    return render_month_board(prev_month(now.strftime("%Y-%m")), now=now, fenced=fenced)


def render_yesterday_board(
    now: datetime.datetime | None = None, fenced: bool = False
) -> str:
    now = now or datetime.datetime.now()
    return render_day_board(
        (now - datetime.timedelta(days=1)).strftime("%Y-%m-%d"), now=now, fenced=fenced
    )


def render_today_board(
    now: datetime.datetime | None = None, fenced: bool = False
) -> str:
    now = now or datetime.datetime.now()
    return render_day_board(now.strftime("%Y-%m-%d"), now=now, fenced=fenced)


def _file_line(path: str) -> str:
    try:
        st = os.stat(path)
        mt = datetime.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        return f"{path}  {st.st_size} 字节  改动 {mt}"
    except OSError:
        return f"{path}  (不存在)"


def render_diag(now: datetime.datetime | None = None, fenced: bool = False) -> str:
    """自检：库在哪、有没有在写、哪天漏算、成员同步成没成功、任务下次何时跑。"""
    now = now or datetime.datetime.now()
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    lines = [f"自检 {now:%Y-%m-%d %H:%M:%S}"]
    latest = (None, None)
    missing = []
    counts = {}
    try:
        c = conn.cursor()
        for t in (
            "msg", "daily_score", "month_score", "members",
            "join_log", "join_history", "score_adjust",
        ):
            try:
                counts[t] = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            except sqlite3.Error:
                counts[t] = -1
        latest = c.execute("SELECT MAX(date), MAX(ts) FROM msg").fetchone()
        last_ts = (
            datetime.datetime.fromtimestamp(latest[1]).strftime("%Y-%m-%d %H:%M:%S")
            if latest and latest[1]
            else "无"
        )
        month = now.strftime("%Y-%m")
        missing = _missing_days(c, month, now.strftime("%Y-%m-%d"))
        try:
            adj_cnt, adj_sum = c.execute(
                "SELECT COUNT(*), COALESCE(SUM(delta), 0) FROM score_adjust WHERE month=?",
                (month,),
            ).fetchone()
        except sqlite3.Error:
            adj_cnt, adj_sum = 0, 0.0
    finally:
        conn.close()

    sched_cfg = get_scheduler_config()
    group_id = get_group_id()
    lines += [
        f"数据库: {_file_line(db_path)}",
        f"数据目录: {get_data_dir()} (在插件目录之外，删插件不会带走)",
        f"遗留库探测: "
        + (", ".join(str(p) for p in legacy_db_candidates()) or "无"),
        f"配置: 群 {group_id or '未配置'} 不计分 {sorted(get_no_score_qqs()) or '无'} "
        f"榜单条数 {'不限' if _limit() <= 0 else _limit()} "
        f"时区 {sched_cfg['timezone'] or '系统本地'}",
        "表行数: " + "  ".join(f"{k}={v}" for k, v in counts.items()),
        f"最近发言: 日期 {latest[0] or '无'} 时间 {last_ts}",
        f"本月待补算日: {', '.join(missing) if missing else '无'}",
        f"本月手工调整: {adj_cnt} 笔 合计 {_fmt_score(adj_sum)} 分（/扣除 记账，重算日分不会抹掉）",
    ]
    if group_id <= 0:
        lines.append(
            "警告: 未配置目标群 group_id，任何发言都不会入库统计，榜单会是空的"
        )
    try:
        from .scheduler import STATE, schedule_info

        s = STATE["sync"]
        lines += [
            f"成员同步: 成功 {s.get('last_ok') or '从未'} 尝试 {s.get('last_attempt') or '-'} "
            f"{s.get('count', 0)} 人 平台 {s.get('platform') or '-'} "
            f"本轮 {s.get('attempts', 0)} 次",
            f"同步错误: {s.get('error') or '无'}",
            f"最近补跑: {STATE['catchup'].get('last_run') or '-'} "
            f"{', '.join(STATE['catchup'].get('recomputed') or []) or '无'}",
        ]
        lines += ["定时任务:"] + [f"  {x}" for x in schedule_info()]
    except Exception as e:  # 独立运行(cli.py)时可能没有 apscheduler
        lines.append(f"定时任务: 不可读({e})")
    return _fence("\n".join(lines), fenced)
