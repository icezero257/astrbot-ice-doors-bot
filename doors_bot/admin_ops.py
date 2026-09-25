"""
doors_bot - 后台管理操作（改数据，仅供 WebUI 指令调用）

所有写操作都遵守同一口径:
- 重置 = 按 msg 原始发言记录重算，不是清空（分数会回来，去重/窗口规则变了也会跟着变）
- 删除数据前先读出被删行并回显，便于误操作后手工回填
- 已结算月份(settled=1)不允许删除/重置其月榜行，需先说明，避免把发过 Robux 的账改乱
"""

from __future__ import annotations

import datetime
import sqlite3

from .daily_score import calc_day
from .monthly_settle import get_month_range, settle
from .runtime_config import (
    get_daily_config,
    get_db_path,
    get_group_id,
    get_monthly_config,
    get_no_score_qqs,
)

_MONTH_FMT = "%Y-%m"
_DATE_FMT = "%Y-%m-%d"


def parse_date(raw: str, today: datetime.date | None = None) -> str | None:
    """把指令参数解析成 YYYY-MM-DD。支持 今天/昨日/前天/裸日(本月内)/完整日期。"""
    today = today or datetime.date.today()
    s = (raw or "").strip()
    if not s:
        return today.strftime(_DATE_FMT)
    if s in ("今天", "今日"):
        return today.strftime(_DATE_FMT)
    if s in ("昨天", "昨日"):
        return (today - datetime.timedelta(days=1)).strftime(_DATE_FMT)
    if s in ("前天",):
        return (today - datetime.timedelta(days=2)).strftime(_DATE_FMT)
    if s.isdigit() and 1 <= int(s) <= 31:
        try:
            return datetime.date(today.year, today.month, int(s)).strftime(_DATE_FMT)
        except ValueError:
            return None
    for fmt in (_DATE_FMT, "%Y/%m/%d", "%m-%d", "%Y%m%d"):
        try:
            return datetime.datetime.strptime(s, fmt).strftime(_DATE_FMT)
        except ValueError:
            continue
    return None


def parse_month(raw: str, now: datetime.date | None = None) -> str | None:
    """把指令参数解析成 YYYY-MM。支持 本月/上月/裸月(今年)/YYYY-MM。"""
    now = now or datetime.date.today()
    s = (raw or "").strip()
    if not s or s in ("本月", "当月"):
        return now.strftime(_MONTH_FMT)
    if s in ("上月", "上个月"):
        return (now.replace(day=1) - datetime.timedelta(days=1)).strftime(_MONTH_FMT)
    if s.isdigit() and 1 <= int(s) <= 12:
        return f"{now.year}-{int(s):02d}"
    for fmt in (_MONTH_FMT, "%Y/%m", "%Y%m"):
        try:
            return datetime.datetime.strptime(s, fmt).strftime(_MONTH_FMT)
        except ValueError:
            continue
    return None


def _month_days_up_to(month: str, today: str) -> list[str]:
    """该月需要重算的日期：历史月全月，当月只到今天（未来的天没有 msg）。"""
    days = get_month_range(month)
    return [d for d in days if d <= today]


def _day_rows(c: sqlite3.Cursor, date_str: str) -> list[tuple]:
    return c.execute(
        "SELECT qq, sentences, score FROM daily_score WHERE date=? ORDER BY score DESC",
        (date_str,),
    ).fetchall()


def _fmt_rows(rows: list[tuple], label: str) -> str:
    if not rows:
        return f"{label}: 无"
    return f"{label}: " + "; ".join(
        f"{int(qq)} {_fmt_score(score)}分/{int(s or 0)}句" for qq, s, score in rows[:20]
    ) + (f" ...(共{len(rows)}行)" if len(rows) > 20 else "")


def _fmt_score(v) -> str:
    return f"{round(float(v or 0), 2):g}"


def _recalc_day(date_str: str, apply_penalty: bool) -> int:
    cfg = get_daily_config()
    return calc_day(
        date_str,
        cfg,
        get_monthly_config(),
        get_group_id(),
        get_no_score_qqs(),
        apply_penalty=apply_penalty,
    )


def reset_day(date_str: str | None = None, now: datetime.date | None = None) -> str:
    """立即重置某一天：删掉该日 daily_score 后按 msg 重算。

    未过完的当天不扣"未发言分"，否则会冤枉晚上才开口的人。
    """
    now = now or datetime.date.today()
    date_str = date_str or now.strftime(_DATE_FMT)
    today = now.strftime(_DATE_FMT)
    if date_str > today:
        return f"重置日 {date_str}: 该日期尚未到来，取消。"

    conn = sqlite3.connect(get_db_path())
    try:
        c = conn.cursor()
        before = _day_rows(c, date_str)
        c.execute("DELETE FROM daily_score WHERE date=?", (date_str,))
        conn.commit()
    finally:
        conn.close()

    people = _recalc_day(date_str, apply_penalty=date_str < today)
    return (
        f"重置日 {date_str} 完成（原 {len(before)} 行，现 {people} 人有分）\n"
        + _fmt_rows(before, "被覆盖的旧记录")
        + ("\n注: 当天尚未过完，未发言扣分要等每日结算时点才生效" if date_str == today else "")
    )


def reset_month(month: str | None = None, now: datetime.date | None = None) -> str:
    """立即重置整月：逐日按 msg 重算（含已漏算的日子），不动结转分与邀请分。"""
    now = now or datetime.date.today()
    month = month or now.strftime(_MONTH_FMT)
    today = now.strftime(_DATE_FMT)
    days = _month_days_up_to(month, today)
    if not days:
        return f"重置月 {month}: 该月尚未开始，取消。"

    conn = sqlite3.connect(get_db_path())
    try:
        c = conn.cursor()
        deleted = c.execute(
            "SELECT COUNT(*) FROM daily_score WHERE date>=? AND date<=?",
            (days[0], days[-1]),
        ).fetchone()[0]
        settled = c.execute(
            "SELECT 1 FROM month_score WHERE month=? AND settled=1 LIMIT 1", (month,)
        ).fetchone()
        c.execute(
            "DELETE FROM daily_score WHERE date>=? AND date<=?", (days[0], days[-1])
        )
        conn.commit()
    finally:
        conn.close()

    for d in days:
        _recalc_day(d, apply_penalty=d < today)
    return (
        f"重置月 {month} 完成: 重算 {len(days)} 天，覆盖 {deleted} 行日分\n"
        + (
            "警告: 该月已结算，月榜总分/Robux 仍是旧值，需要时再执行 /结算 " + month
            if settled
            else "注: 结转分(carry)与邀请分(bonus)保持不变"
        )
    )


def delete_board_row(
    qq: int, month: str | None = None, now: datetime.date | None = None
) -> str:
    """删除某人本月的全部记录（日分 + 月榜行），下次计分从零开始。"""
    now = now or datetime.date.today()
    month = month or now.strftime(_MONTH_FMT)
    today = now.strftime(_DATE_FMT)
    days = _month_days_up_to(month, today)
    if not days:
        return f"删除 {qq} {month}: 该月尚未开始，取消。"

    conn = sqlite3.connect(get_db_path())
    try:
        c = conn.cursor()
        if c.execute(
            "SELECT 1 FROM month_score WHERE qq=? AND month=? AND settled=1", (qq, month)
        ).fetchone():
            return f"删除 {qq} {month}: 该月已结算，为保住已发的 Robux 账目拒绝删除。"
        day_rows = c.execute(
            "SELECT qq, sentences, score FROM daily_score "
            "WHERE qq=? AND date>=? AND date<=? ORDER BY date",
            (qq, days[0], days[-1]),
        ).fetchall()
        month_rows = c.execute(
            "SELECT qq, total, carry, bonus, robux FROM month_score WHERE qq=? AND month=?",
            (qq, month),
        ).fetchall()
        if not day_rows and not month_rows:
            return f"删除 {qq} {month}: 该人本月没有任何记录，未做改动。"
        c.execute(
            "DELETE FROM daily_score WHERE qq=? AND date>=? AND date<=?",
            (qq, days[0], days[-1]),
        )
        c.execute("DELETE FROM month_score WHERE qq=? AND month=?", (qq, month))
        conn.commit()
    finally:
        conn.close()

    return (
        f"删除 {qq} {month} 完成: 日分 {len(day_rows)} 行、月榜 {len(month_rows)} 行已删除，"
        "该人本月从零重新计分\n"
        + _fmt_rows(list(day_rows), "被删日分(如需回填照此手工改)")
        + (f"\n被删月榜行: {month_rows}" if month_rows else "")
    )


def settle_month(
    month: str | None = None, now: datetime.date | None = None, force: bool = False
) -> str:
    """立即结算某月（幂等，可重复执行）。未过完的月份需要 force 确认。"""
    now = now or datetime.date.today()
    month = month or (now.replace(day=1) - datetime.timedelta(days=1)).strftime(_MONTH_FMT)
    days = get_month_range(month)
    if not force and days[-1] > now.strftime(_DATE_FMT):
        return (
            f"结算 {month}: 该月尚未结束，现在结分会漏掉剩余天数。\n"
            f"确认照此结算请发: /结算 {month} 确认"
        )

    # 先把该月漏算的日子补齐，再结算
    from .scheduler import missing_calc_days, run_catchup

    today = now.strftime(_DATE_FMT)
    missing = missing_calc_days(month, min(days[-1], today))
    caught = run_catchup(missing, now=now) if missing else []

    summary = settle(month, get_monthly_config())
    return (
        f"结算 {month} 完成: {summary['participants']} 人参与，"
        f"{summary['winners']} 人获奖\n"
        f"发放名单: {summary['pay_list_path']}"
        + (f"\n结算前补跑: {', '.join(caught)}" if caught else "")
    )


def manual_catchup(now: datetime.date | None = None) -> str:
    """立即补跑所有"有发言记录却没有日分"的已过日期。"""
    from .scheduler import pending_catchup_days, run_catchup

    targets = pending_catchup_days(now)
    if not targets:
        return "补跑: 没有漏算的日期，一切正常。"
    done = run_catchup(targets, now=now)
    return f"补跑完成 {len(done)} 天: {', '.join(done)}" if done else "补跑失败，详见 AstrBot 日志"


# ================= 二次确认 =================
# 所有会改数据的指令都先回显"将要改什么"，只有再发一次带 确认 的同一条命令才真执行。

CONFIRM_WORD = "确认"


def split_confirm(raw_args: str) -> tuple[list[str], bool]:
    """把参数里的"确认"摘掉，返回 (其余参数, 是否已确认)。"""
    parts = (raw_args or "").split()
    return [p for p in parts if p != CONFIRM_WORD], CONFIRM_WORD in parts


def confirm_hint(cmd: str, args: list[str]) -> str:
    return "未执行。确认无误后原样再发一次: /" + " ".join([cmd, *args, CONFIRM_WORD])


def _settled_month(c: sqlite3.Cursor, month: str) -> bool:
    return bool(
        c.execute(
            "SELECT 1 FROM month_score WHERE month=? AND settled=1 LIMIT 1", (month,)
        ).fetchone()
    )


def _month_row(c: sqlite3.Cursor, qq: int, month: str) -> str:
    """某人某月的家底：日分行数/合计/句数、结转、邀请、已扣、是否已结算。"""
    days = get_month_range(month)
    daily, rows, sents = c.execute(
        "SELECT COALESCE(SUM(score),0), COUNT(*), COALESCE(SUM(sentences),0) "
        "FROM daily_score WHERE qq=? AND date>=? AND date<=?",
        (qq, days[0], days[-1]),
    ).fetchone()
    carry, bonus = c.execute(
        "SELECT COALESCE(carry,0), COALESCE(bonus,0) FROM month_score "
        "WHERE qq=? AND month=?",
        (qq, month),
    ).fetchone() or (0.0, 0.0)
    adj, adj_rows = c.execute(
        "SELECT COALESCE(SUM(delta),0), COUNT(*) FROM score_adjust WHERE qq=? AND month=?",
        (qq, month),
    ).fetchone()
    return (
        f"日分 {rows} 行/{int(sents or 0)} 句 = {_fmt_score(daily)} 分，"
        f"结转 {_fmt_score(carry)}，邀请 {_fmt_score(bonus)}，已手工调整 {_fmt_score(adj)}({adj_rows} 笔)，"
        f"该月{'已' if _settled_month(c, month) else '未'}结算"
    )


def plan_reset_day(date_str: str, now: datetime.date | None = None) -> str:
    now = now or datetime.date.today()
    today = now.strftime(_DATE_FMT)
    if date_str > today:
        return f"重置日 {date_str}: 该日期尚未到来，没有可重算的内容。"
    conn = sqlite3.connect(get_db_path())
    try:
        rows = _day_rows(conn.cursor(), date_str)
    finally:
        conn.close()
    total = round(sum(float(r[2] or 0) for r in rows), 2)
    return (
        f"重置日 {date_str}: 将删掉现有 {len(rows)} 行日分（合计 {_fmt_score(total)} 分），"
        "再按 msg 原始发言重算。改过计分参数后重算结果会和现在不同；"
        "msg 发言记录与月榜/结转/手工调整都不受影响。"
        + ("\n注: 当天尚未过完，重算不扣未发言分" if date_str == today else "")
    )


def plan_reset_month(month: str, now: datetime.date | None = None) -> str:
    now = now or datetime.date.today()
    days = _month_days_up_to(month, now.strftime(_DATE_FMT))
    if not days:
        return f"重置月 {month}: 该月尚未开始，没有可重算的内容。"
    conn = sqlite3.connect(get_db_path())
    try:
        c = conn.cursor()
        rows, total = c.execute(
            "SELECT COUNT(*), COALESCE(SUM(score),0) FROM daily_score "
            "WHERE date>=? AND date<=?",
            (days[0], days[-1]),
        ).fetchone()
        settled = _settled_month(c, month)
    finally:
        conn.close()
    return (
        f"重置月 {month}: 将逐日重算 {len(days)} 天（{days[0]} ~ {days[-1]}），"
        f"覆盖现有 {rows} 行日分（合计 {_fmt_score(total)} 分）。"
        "结转分、邀请分、手工调整都不受影响。"
        + (
            "\n警告: 该月已结算，重算日分不会自动改写月榜与发放名单，"
            f"需要重结请再执行 /结算 {month}"
            if settled
            else ""
        )
    )


def plan_delete(qq: int, month: str, now: datetime.date | None = None) -> str:
    now = now or datetime.date.today()
    days = _month_days_up_to(month, now.strftime(_DATE_FMT))
    if not days:
        return f"删除 {qq} {month}: 该月尚未开始，没有可删的记录。"
    conn = sqlite3.connect(get_db_path())
    try:
        c = conn.cursor()
        state = _month_row(c, qq, month)
        settled = c.execute(
            "SELECT 1 FROM month_score WHERE qq=? AND month=? AND settled=1", (qq, month)
        ).fetchone()
    finally:
        conn.close()
    head = f"删除 {qq} {month}: 将删掉该人本月全部日分与月榜行，下次计分从零开始。\n现状 {state}"
    if settled:
        return head + "\n注意: 该人已结算（Robux 账目已定），执行时会被拒绝，本指令改不动已结算的月份。"
    return head + "\n删除前会先回显被删的行，方便手工回填。"


def plan_settle(month: str, now: datetime.date | None = None) -> str:
    now = now or datetime.date.today()
    days = get_month_range(month)
    conn = sqlite3.connect(get_db_path())
    try:
        c = conn.cursor()
        people = c.execute(
            "SELECT COUNT(DISTINCT qq) FROM daily_score WHERE date>=? AND date<=?",
            (days[0], days[-1]),
        ).fetchone()[0]
        settled = _settled_month(c, month)
    finally:
        conn.close()
    cfg = get_monthly_config()
    tail = []
    if days[-1] > now.strftime(_DATE_FMT):
        tail.append(
            f"警告: 该月尚未结束（还剩到 {days[-1]}），现在结分会漏掉之后的发言分"
        )
    if settled:
        tail.append("该月已有结算结果，重新结算会覆盖月榜总分/Robux 并重写发放名单文件")
    tail.append(
        f"参与 {people} 人；奖金口径 前{int(cfg['top_n'])}名各{int(cfg['award_per_top'])}R"
        "（并列只平分第N名那一份）；结转与邀请分一并计入"
    )
    return f"结算 {month}: 先补跑该月漏算日，再按月结算规则重算并生成发放名单文件。\n" + "\n".join(tail)


def plan_catchup(now: datetime.date | None = None) -> str:
    from .scheduler import pending_catchup_days

    targets = pending_catchup_days(now)
    if not targets:
        return "补跑: 没有「有发言记录却没有日分」的日期，执行了也不会改动任何数据。"
    return (
        f"补跑: 将按 msg 重算 {len(targets)} 天的日分（{', '.join(targets)}），"
        "只补没有日分的日期，已有日分的天不动。"
    )


def plan_deduct(
    qq: int, points: float, month: str, now: datetime.date | None = None
) -> str:
    if points <= 0:
        return "扣除的分数要写正数，例如 /扣除 123456789 1"
    if qq in get_no_score_qqs():
        return f"扣除 {qq}: 该QQ在不计分名单里，本来就不上榜，没有可扣的分。"
    conn = sqlite3.connect(get_db_path())
    try:
        state = _month_row(conn.cursor(), qq, month)
    finally:
        conn.close()
    return (
        f"扣除 {qq} {month} {_fmt_score(points)} 分（写入 score_adjust，"
        f"重算日分不会抹掉，只在该月结算时计入总分，总分最低扣到 0）。\n现状 {state}"
    )


def deduct_score(
    qq: int,
    points: float,
    month: str | None = None,
    reason: str = "",
    now: datetime.date | None = None,
) -> str:
    """记一笔手工扣分（负 delta），该月总分随之减少，结算时生效。"""
    now = now or datetime.date.today()
    month = month or now.strftime(_MONTH_FMT)
    conn = sqlite3.connect(get_db_path())
    try:
        c = conn.cursor()
        if c.execute(
            "SELECT 1 FROM month_score WHERE qq=? AND month=? AND settled=1", (qq, month)
        ).fetchone():
            return f"扣除 {qq} {month}: 该月已结算，为保住已发的 Robux 账目拒绝扣分。"
        c.execute(
            "INSERT INTO score_adjust(qq, month, delta, reason, created) VALUES(?,?,?,?,?)",
            (
                qq,
                month,
                -abs(float(points)),
                reason or "后台手工扣分",
                now.strftime(_DATE_FMT),
            ),
        )
        conn.commit()
        after = c.execute(
            "SELECT SUM(COALESCE(delta,0)) FROM score_adjust WHERE qq=? AND month=?",
            (qq, month),
        ).fetchone()[0]
    finally:
        conn.close()
    return (
        f"扣除 {qq} {month} {_fmt_score(points)} 分完成"
        f"（该月累计手工调整 {_fmt_score(after)} 分）"
        + (f"，原因: {reason}" if reason else "")
        + "\n注: 当日/当月榜单是实时算的，日榜不会变，月榜与下次结算才会体现。"
    )
