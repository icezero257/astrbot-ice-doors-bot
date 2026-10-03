"""
doors_bot - 每日计分

规则:
- 每句有效发言 per_msg_score 分，单日发言分封顶 daily_cap
- 当日有效句数 >= bonus_threshold → 额外 +bonus_score
- 当日未发有效发言的在群成员 → -penalty（从当月已有分数中扣，扣完为止，不为负）
- 有效发言 = 纯文本 + 5秒去重(入库时) + 前后 window 秒内不同发言人数 >= min_count
  (窗口判定在计分/查榜时进行，可追溯：第二人开口后第一人窗内消息也计分)
- 邀请奖励(可选): 新人入群起 window_days 天内每日有效发言均 > daily_min_msgs，
  或日均 >= avg_daily_msgs → 邀请人 +bonus 分，记在**新人入群那个月**的 month_score.bonus
  （那个月已经结算完就退到当前月，否则这笔分只进榜、换不来 Robux）

用法:
  python -m doors_bot.daily_score            # 计算今天
  python -m doors_bot.daily_score 2026-01-15 # 计算指定日期
"""

from __future__ import annotations

import datetime
import sqlite3
import sys

from .runtime_config import (
    estimate_day_score,
    get_daily_config,
    get_db_path,
    get_group_id,
    get_monthly_config,
    get_no_score_qqs,
)


def _month_prefix(date_str: str) -> str:
    return date_str[:7]


def valid_msg_counts(
    conn: sqlite3.Connection,
    group_id: int,
    date_str: str,
    window_seconds: int,
    min_count: int,
    exclude_qqs: set[int] | None = None,
) -> dict[int, int]:
    """某人一条消息计为有效，当且仅当其前后 window 秒内(含本人)出现 >= min_count 个不同发言者。

    exclude_qqs 默认取配置的不计分QQ：他们既不被计分，也不充当"有人接话"的佐证，
    所以连历史上已入库的发言也不会替机器人把别人抬进榜。
    """
    excl = sorted(
        int(q) for q in (get_no_score_qqs() if exclude_qqs is None else exclude_qqs)
    )
    # 值都是转成 int 的QQ号，直接内联进 SQL 不引入注入面
    skip = lambda alias: (  # noqa: E731
        f" AND {alias}.qq NOT IN ({', '.join(str(q) for q in excl)})" if excl else ""
    )
    rows = conn.execute(
        f"""
        SELECT m.qq, COUNT(*)
        FROM msg m
        WHERE m.group_id=? AND m.date=?{skip('m')}
          AND (
            SELECT COUNT(DISTINCT m2.qq)
            FROM msg m2
            WHERE m2.group_id=m.group_id{skip('m2')}
              AND m2.ts BETWEEN m.ts-? AND m.ts+?
          ) >= ?
        GROUP BY m.qq
        """,
        (group_id, date_str, window_seconds, window_seconds, max(2, min_count)),
    ).fetchall()
    return {int(qq): int(cnt) for qq, cnt in rows}


def roster_with_join_dates(
    conn: sqlite3.Connection, group_id: int
) -> dict[int, str]:
    """扣分名单。members(每小时同步)优先：in_group=0 的退群者不扣；
    join_log 补充从未同步到的人。日期取最早可得的入群日期。"""
    hist_first = {
        int(qq): d
        for qq, d in conn.execute(
            "SELECT qq, MIN(join_date) FROM join_history GROUP BY qq"
        )
    }

    roster: dict[int, str] = {}
    synced: set[int] = set()
    try:
        member_rows = conn.execute(
            "SELECT qq, join_date, COALESCE(in_group, 1) FROM members WHERE group_id=?",
            (group_id,),
        ).fetchall()
    except sqlite3.Error:
        member_rows = []

    for qq, jd, in_group in member_rows:
        qq = int(qq)
        synced.add(qq)
        if not in_group:
            continue  # 已退群
        date = hist_first.get(qq) or jd
        if date:
            roster[qq] = date

    for qq, jd in conn.execute(
        "SELECT qq, join_date FROM join_log WHERE group_id=?", (group_id,)
    ):
        qq = int(qq)
        if qq in synced:
            continue  # 已同步的成员表(含退群标记)优先
        date = hist_first.get(qq) or jd
        if date:
            roster.setdefault(qq, date)
    return roster


def _available_score(
    cursor: sqlite3.Cursor, qq: int, date_str: str, month: str
) -> float:
    """扣分上限: 当月该日之前已积累的日分 + 当月结转/邀请分，最低0。"""
    row = cursor.execute(
        """
        SELECT COALESCE(SUM(score), 0)
        FROM daily_score
        WHERE qq=? AND date LIKE ? AND date < ?
        """,
        (qq, f"{month}%", date_str),
    ).fetchone()
    month_row = cursor.execute(
        """
        SELECT COALESCE(carry, 0) + COALESCE(bonus, 0)
        FROM month_score WHERE qq=? AND month=?
        """,
        (qq, month),
    ).fetchone()
    prior = float(month_row[0]) if month_row else 0.0
    return max(0.0, round(float(row[0] or 0) + prior, 2))


def check_invite_bonus(
    conn: sqlite3.Connection,
    today: str,
    group_id: int,
    monthly_cfg: dict,
    daily_cfg: dict,
) -> int:
    """评估邀请奖励: 入群满 window_days 天的新人。返回本次发奖人数。

    观察期满的那天才算得完"每天都达标/日均达标"，所以奖励就是那晚入账（再早也没有
    完整数据）；入账月份取新人入群的那个月，见下面 awarded_month 的注释。
    """
    if not monthly_cfg.get("invite_enabled", True):
        return 0

    window_days = int(monthly_cfg.get("invite_window_days", 10))
    daily_min = int(monthly_cfg.get("invite_daily_min_msgs", 10))
    avg_min = int(monthly_cfg.get("invite_avg_daily_msgs", 20))
    bonus = int(monthly_cfg.get("invite_bonus", 10))

    candidates = conn.execute(
        """
        SELECT j.qq, j.invited_by, j.join_date
        FROM join_log j
        LEFT JOIN newbie_bonus n ON n.newbie_qq = j.qq
        WHERE j.group_id=? AND j.invited_by != 0 AND n.newbie_qq IS NULL
          AND (SELECT COUNT(*) FROM join_history h WHERE h.qq = j.qq) <= 1
          -- 管理员审批放行的那次入群，事件里带的人是审批人而不是拉人的人，
          -- 不算邀请、也不给分；除非有人把它确认下来(invited_src 2=本人绑定 3=后台代填)。
          AND (COALESCE(j.join_kind, '') != 'approve'
               OR COALESCE(j.invited_src, 0) >= 2)
        """,
        (group_id,),
    ).fetchall()

    awarded = 0
    today_date = datetime.date.fromisoformat(today)

    for newbie_qq, inviter_qq, join_date in candidates:
        try:
            join = datetime.date.fromisoformat(join_date)
        except (TypeError, ValueError):
            continue
        end = join + datetime.timedelta(days=window_days - 1)
        if today_date < end:
            continue  # 观察期未满

        per_day: list[int] = []
        d = join
        while d <= end and d <= today_date:
            counts = valid_msg_counts(
                conn,
                group_id,
                d.strftime("%Y-%m-%d"),
                daily_cfg["multi_speaker_window_seconds"],
                daily_cfg["multi_speaker_min_count"],
            )
            per_day.append(counts.get(int(newbie_qq), 0))
            d += datetime.timedelta(days=1)

        if len(per_day) < window_days:
            continue  # 观察期数据不完整

        cond_a = all(cnt > daily_min for cnt in per_day)
        cond_b = (sum(per_day) / len(per_day)) >= avg_min
        if not (cond_a or cond_b):
            continue

        # 这笔分记到新人入群的那个月（谁在几月拉的人就算在几月的账上），而不是
        # 观察期满的那天所在的下个月。入群月已经封账就退到当前月——已结算的月份
        # total/robux 都定死了，再往里加分只改榜单、换不来钱。
        awarded_month = join.strftime("%Y-%m")
        closed = conn.execute(
            "SELECT 1 FROM month_score WHERE month=? AND settled=1 LIMIT 1",
            (awarded_month,),
        ).fetchone()
        if closed:
            awarded_month = today[:7]
        try:
            conn.execute(
                """
                INSERT INTO newbie_bonus
                    (newbie_qq, inviter_qq, awarded_month, bonus)
                VALUES (?, ?, ?, ?)
                """,
                (newbie_qq, inviter_qq, awarded_month, bonus),
            )
        except sqlite3.IntegrityError:
            continue  # 已奖过

        conn.execute(
            """
            INSERT INTO month_score (qq, month)
            VALUES (?, ?)
            ON CONFLICT(qq, month) DO NOTHING
            """,
            (inviter_qq, awarded_month),
        )
        conn.execute(
            """
            UPDATE month_score
            SET bonus = COALESCE(bonus, 0) + ?
            WHERE qq=? AND month=?
            """,
            (bonus, inviter_qq, awarded_month),
        )
        awarded += 1
        print(
            f"[daily_score] 邀请奖励: 新人{newbie_qq} 达标，"
            f"邀请人{inviter_qq} +{bonus}分 ({awarded_month})"
            + ("；入群月已结算，改记当前月" if closed else "")
        )

    conn.commit()
    return awarded


def calc_day(
    date_str: str,
    daily_cfg: dict | None = None,
    monthly_cfg: dict | None = None,
    group_id: int | None = None,
    exclude_qqs: set[int] | None = None,
    apply_penalty: bool = True,
) -> int:
    """结算某一天的得分。返回当日有有效发言的人数。

    apply_penalty=False 用于"还没过完的一天"（手动重置今天），
    否则会误伤当天晚些时候才开口的人。
    """
    cfg = daily_cfg or get_daily_config()
    monthly_cfg = monthly_cfg or get_monthly_config()
    group_id = group_id if group_id is not None else get_group_id()
    exclude_qqs = get_no_score_qqs() if exclude_qqs is None else exclude_qqs

    conn = sqlite3.connect(get_db_path())
    try:
        c = conn.cursor()
        counts = valid_msg_counts(
            conn,
            group_id,
            date_str,
            cfg["multi_speaker_window_seconds"],
            cfg["multi_speaker_min_count"],
        )

        for qq, sentences in counts.items():
            score = estimate_day_score(int(sentences), cfg)
            c.execute(
                """
                INSERT OR REPLACE INTO daily_score(qq, date, score, sentences)
                VALUES(?,?,?,?)
                """,
                (qq, date_str, score, int(sentences)),
            )

        # 未发言扣分
        penalty = float(cfg["penalty"])
        month = _month_prefix(date_str)
        roster = roster_with_join_dates(conn, group_id)
        penalized = 0
        if apply_penalty:
            for qq, join_date in roster.items():
                if qq in exclude_qqs or qq in counts or join_date > date_str:
                    continue
                available = _available_score(c, qq, date_str, month)
                applied = round(min(penalty, available), 2)
                if applied <= 0:
                    continue
                c.execute(
                    """
                    INSERT OR REPLACE INTO daily_score(qq, date, score, sentences)
                    VALUES(?,?,?,0)
                    """,
                    (qq, date_str, -applied),
                )
                penalized += 1

        check_invite_bonus(conn, date_str, group_id, monthly_cfg, cfg)

        conn.commit()
    finally:
        conn.close()

    print(
        f"[daily_score] {date_str} 计算完成: "
        f"{len(counts)} 人有效发言, {penalized} 人扣分"
        + ("" if apply_penalty else "（未过完的一天，跳过未发言扣分）")
    )
    return len(counts)


if __name__ == "__main__":
    target = (
        sys.argv[1]
        if len(sys.argv) > 1
        else datetime.date.today().strftime("%Y-%m-%d")
    )
    calc_day(target)
