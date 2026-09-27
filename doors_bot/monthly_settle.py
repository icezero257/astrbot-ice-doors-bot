"""
doors_bot - 月度结算

月度总分 = max(0, 当月每日得分之和) × 新人系数 × 连续获奖衰减系数
           + 上月结转(carry) + 邀请奖励分(bonus) + 手工调整分(score_adjust，/扣除 记负分)

- 新人系数: 入群当月及次月 ×newbie_multiplier
- 衰减系数: 连续 n 个月获奖，本月 ×(1 - decay_rate×n)，最低到 0
- 奖金池固定 top_n × award_per_top（默认 500R），永不超发：前 N 名含并列，
  并列的人整组平分"这组占掉的剩余名额"，装得下就每人一份，装不下就整组按比例缩
- 获奖者下月结转清零；未获奖者 总分×carry_over 计入下月 carry
- Robux 应发数写入 month_score.robux 列，并导出 pay_list_YYYY-MM.txt
"""

from __future__ import annotations

import datetime
import math
import os
import sqlite3
import sys

from .runtime_config import get_db_path, get_monthly_config, get_pay_list_dir


def get_month_range(month_str: str):
    y, m = map(int, month_str.split("-"))
    first = datetime.date(y, m, 1)
    last = (
        datetime.date(y + 1, 1, 1) - datetime.timedelta(days=1)
        if m == 12
        else datetime.date(y, m + 1, 1) - datetime.timedelta(days=1)
    )
    days = []
    d = first
    while d <= last:
        days.append(d.strftime("%Y-%m-%d"))
        d += datetime.timedelta(days=1)
    return days


def prev_month(month_str: str) -> str:
    y, m = map(int, month_str.split("-"))
    return f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"


def next_month(month_str: str) -> str:
    y, m = map(int, month_str.split("-"))
    return f"{y + 1}-01" if m == 12 else f"{y}-{m + 1:02d}"


def get_join_date(c: sqlite3.Cursor, qq: int):
    row = c.execute(
        "SELECT join_date FROM join_log WHERE qq=?", (qq,)
    ).fetchone()
    if not row or not row[0]:
        row = c.execute(
            "SELECT join_date FROM members WHERE qq=? AND join_date IS NOT NULL "
            "ORDER BY group_id LIMIT 1",
            (qq,),
        ).fetchone()
    if not row or not row[0]:
        return None
    try:
        return datetime.date.fromisoformat(row[0])
    except ValueError:
        return None


def is_newbie(qq: int, month_str: str, c: sqlite3.Cursor) -> bool:
    """入群当月及次月视为新人。

    防"退了又进"卡加成: 新人 = 从未进过群的人。join_history 是入群事件流水，
    同一 QQ 出现 >=2 条即视为回流成员，永久失去新人加成与邀请奖励资格。
    """
    try:
        joins = c.execute(
            "SELECT COUNT(*) FROM join_history WHERE qq=?", (qq,)
        ).fetchone()[0]
    except sqlite3.Error:
        joins = 0
    if joins >= 2:
        return False

    join = get_join_date(c, qq)
    if not join:
        return False
    y, m = map(int, month_str.split("-"))
    cur = datetime.date(y, m, 1)
    last_month = (
        datetime.date(y - 1, 12, 1) if m == 1 else datetime.date(y, m - 1, 1)
    )
    return join.year == cur.year and join.month == cur.month or (
        join.year == last_month.year and join.month == last_month.month
    )


def consecutive_award_count(qq: int, before_month: str, c: sqlite3.Cursor) -> int:
    y, m = map(int, before_month.split("-"))
    cur = datetime.date(y, m, 1)
    count = 0
    while count <= 100:
        cur = (
            datetime.date(cur.year - 1, 12, 1)
            if cur.month == 1
            else datetime.date(cur.year, cur.month - 1, 1)
        )
        if c.execute(
            "SELECT 1 FROM award_history WHERE qq=? AND month=?",
            (qq, cur.strftime("%Y-%m")),
        ).fetchone():
            count += 1
        else:
            break
    return count


def _money(v: float) -> float:
    """金额向下取到 2 位小数：平分后每人各拿一样的数，宁可少发 1 分也不超发。"""
    return math.floor(float(v) * 100) / 100


def compute_awards(ranking, top_n: int, award_per_top: int) -> dict[int, float]:
    """ranking: [(qq, total) 按总分降序]。返回 qq -> 应发Robux。

    奖金池固定 `top_n × award_per_top`（默认 10×50=500R），一分都不会超发。
    名次并列共享，所以整组并列的人必须拿一样多：
    - 并列组装得下剩余名额（前 7 人无并列，第 8 名 3 人并列正好占第 8~10 顺位）
      → 每人拿满一份 50R，名额按占掉的顺位数扣掉；
    - 装不下（第 7 名起 7 人并列，顺位排到第 13 名）→ 这 7 人平分第 7~10 名
      那 (10-7+1)×50=200R，每人 28.57R，池子见底，后面的人不再有奖金。
    """
    awards: dict[int, float] = {}
    if not ranking or top_n < 1:
        return awards
    slots = int(top_n)  # 剩余名额，每个名额值 award_per_top
    i, n = 0, len(ranking)
    while i < n and slots > 0:
        j = i
        while j < n and abs(float(ranking[j][1]) - float(ranking[i][1])) < 0.005:
            j += 1
        group = ranking[i:j]
        if len(group) <= slots:
            share = float(award_per_top)
            slots -= len(group)
        else:
            share = _money(slots * float(award_per_top) / len(group))
            slots = 0
        for qq, _total in group:
            awards[int(qq)] = share
        i = j
    return awards


def adjust_totals(c: sqlite3.Cursor, month_str: str) -> dict[int, float]:
    """该月的手工调整合计（/扣除 为负）。独立于 daily_score，重算日分不会抹掉它。"""
    return {
        int(qq): round(float(delta or 0), 2)
        for qq, delta in c.execute(
            "SELECT qq, SUM(COALESCE(delta,0)) FROM score_adjust "
            "WHERE month=? GROUP BY qq",
            (month_str,),
        )
    }


def settle(month_str: str, config: dict | None = None) -> dict:
    """结算 month_str 月。可重复执行(幂等)。返回结算摘要。"""
    cfg = {**(config or get_monthly_config())}
    conn = sqlite3.connect(get_db_path())
    try:
        c = conn.cursor()
        days = get_month_range(month_str)

        # 1. 当月每日得分之和
        daily_sum: dict[int, float] = {}
        placeholders = ",".join("?" * len(days))
        for qq, score in c.execute(
            f"""
            SELECT qq, SUM(COALESCE(score, 0))
            FROM daily_score
            WHERE date IN ({placeholders})
            GROUP BY qq
            """,
            days,
        ):
            daily_sum[int(qq)] = float(score or 0)

        # 2. 已有结转/邀请分
        credits: dict[int, tuple[float, float]] = {}
        for qq, carry, bonus in c.execute(
            "SELECT qq, COALESCE(carry,0), COALESCE(bonus,0) FROM month_score WHERE month=?",
            (month_str,),
        ):
            credits[int(qq)] = (float(carry), float(bonus))

        # 2b. 手工调整（/扣除 等），在系数之后直接加减总分，不受日分重算影响
        adjust = adjust_totals(c, month_str)

        qqs = set(daily_sum) | set(credits) | set(adjust)

        # 3. 套系数 + 加结转
        finals: dict[int, float] = {}
        for qq in qqs:
            base = max(0.0, round(daily_sum.get(qq, 0.0), 2))
            if is_newbie(qq, month_str, c):
                base = round(base * cfg["newbie_multiplier"], 2)
            n = consecutive_award_count(qq, month_str, c)
            if n > 0:
                factor = max(0.0, 1 - cfg["decay_rate"] * n)
                base = round(base * factor, 2)
            carry, bonus = credits.get(qq, (0.0, 0.0))
            finals[qq] = max(0.0, round(base + carry + bonus + adjust.get(qq, 0.0), 2))

        # 4. 排名
        ranking = sorted(finals.items(), key=lambda x: x[1], reverse=True)

        # 5. 奖励判定（奖金池 top_n 份，并列整组平分，总额封顶）
        top_n = int(cfg["top_n"])
        award_per_top = int(cfg["award_per_top"])
        awards = compute_awards(ranking, top_n, award_per_top)
        winner_set = {qq for qq, r in awards.items() if r > 0}

        # 6. 落库（含 Robux 列）
        nm = next_month(month_str)
        next_settled = c.execute(
            "SELECT 1 FROM month_score WHERE month=? AND settled=1 LIMIT 1",
            (nm,),
        ).fetchone()

        carry_over = float(cfg["carry_over"])
        for rank, (qq, total) in enumerate(ranking, start=1):
            robux = awards.get(qq, 0.0)
            c.execute(
                """
                INSERT INTO month_score(qq, month, total, rank, robux, settled)
                VALUES(?,?,?,?,?,1)
                ON CONFLICT(qq, month) DO UPDATE SET
                    total=excluded.total,
                    rank=excluded.rank,
                    robux=excluded.robux,
                    settled=1
                """,
                (qq, month_str, total, rank, robux),
            )
            if robux > 0:
                c.execute(
                    "INSERT OR IGNORE INTO award_history(qq, month) VALUES(?,?)",
                    (qq, month_str),
                )
                carry = 0.0
            else:
                carry = round(total * carry_over, 2)

            if not next_settled:
                c.execute(
                    """
                    INSERT INTO month_score(qq, month, carry)
                    VALUES(?,?,?)
                    ON CONFLICT(qq, month) DO UPDATE SET carry=excluded.carry
                    """,
                    (qq, nm, carry),
                )

        conn.commit()

        names = {
            int(qq): (name or str(qq))
            for qq, name in c.execute("SELECT qq, name FROM user_info")
        }

        rows = [
            {
                "rank": rank,
                "qq": qq,
                "name": names.get(qq, str(qq)),
                "total": total,
                "robux": awards.get(qq, 0.0),
            }
            for rank, (qq, total) in enumerate(ranking, start=1)
        ]

        # 7. 导出发放名单（数据库侧/文件侧保留 Robux 列），与库同目录以免随插件被删
        out_path = os.path.join(
            str(get_pay_list_dir()), f"pay_list_{month_str}.txt"
        )
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(
                f"# {month_str} Robux 发放名单"
                f"（奖金池 {top_n * award_per_top}R：前 {top_n} 名含并列，"
                f"并列整组平分剩余名额）\n"
            )
            f.write(f"{'排名':<6}{'QQ':<14}{'昵称':<16}{'总分':<10}{'Robux'}\n")
            for row in rows:
                if row["robux"] <= 0:
                    continue
                f.write(
                    f"{row['rank']:<6}{row['qq']:<14}{row['name']:<16}"
                    f"{row['total']:<10}{_money(row['robux']):g}\n"
                )
            f.write(
                f"# 获奖 {len(winner_set)} 人，实发合计 {_money(sum(awards.values())):g}R"
                f"（奖金池 {top_n * award_per_top}R，平分后不足 1R 的零头留在池子里）\n"
            )

        summary = {
            "month": month_str,
            "participants": len(ranking),
            "winners": len(winner_set),
            "rows": rows,
            "pay_list_path": out_path,
        }
    finally:
        conn.close()

    print(
        f"[monthly_settle] {month_str} 结算完成: "
        f"{len(ranking)} 人参与, 获奖 {len(winner_set)} 人, 名单: {out_path}"
    )
    return summary


if __name__ == "__main__":
    if len(sys.argv) > 1:
        target_month = sys.argv[1]
    else:
        today = datetime.date.today()
        target_month = (
            f"{today.year - 1}-12" if today.month == 1 else f"{today.year}-{today.month - 1:02d}"
        )
    settle(target_month)
