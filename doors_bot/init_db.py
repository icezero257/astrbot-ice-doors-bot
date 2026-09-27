"""
doors_bot - 数据库初始化与无损迁移

表:
  msg           原始有效候选消息（计分口径在查询时按多人窗口判定）
  daily_score   每日得分缓存
  month_score   月度总分: total=结算后的最终分, carry=上月结转, bonus=邀请奖励分,
                rank=结算排名, robux=应发Robux, settled=是否已结算
  join_log      入群记录（最新一次，含邀请人；invited_src 标出邀请人是事件检测的还是人工绑定的）
  join_history  入群事件流水（只追加；同 qq 多条 = 退了又进，永久失去新人资格）
  pending_bind  待人工确认的邀请关系（新人入群时开一个时限，超时或绑定成功即失效）
  members       NapCat 每小时同步的全群成员名单（未发言扣分名单，in_group 标记退群）
  user_info     QQ昵称缓存（榜单展示）
  bind          QQ <-> Roblox 绑定
  award_history 获奖历史（连续获奖衰减）
  newbie_bonus  邀请奖励发放记录（防重复）
"""

from __future__ import annotations

import os
import sqlite3

from .runtime_config import get_db_path


def _table_columns(cursor: sqlite3.Cursor, table: str) -> set[str]:
    rows = cursor.execute(f"PRAGMA table_info({table})").fetchall()
    return {row[1] for row in rows}


def _ensure_column(
    cursor: sqlite3.Cursor,
    table: str,
    column: str,
    col_def: str,
) -> bool:
    """列不存在则添加，返回本次是否新建了该列。"""
    if column not in _table_columns(cursor, table):
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {col_def}")
        return True
    return False


def init(db_path: str | None = None) -> str:
    path = db_path or get_db_path()
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(path)
    c = conn.cursor()

    c.execute(
        """
        CREATE TABLE IF NOT EXISTS msg (
            qq        INTEGER NOT NULL,
            group_id  INTEGER NOT NULL,
            date      TEXT    NOT NULL,
            ts        INTEGER NOT NULL,
            content   TEXT
        )
        """
    )
    _ensure_column(c, "msg", "content", "content TEXT")
    c.execute("CREATE INDEX IF NOT EXISTS idx_msg_qq_date ON msg(qq, date)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_msg_group_ts ON msg(group_id, ts)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_msg_group_date ON msg(group_id, date)")

    c.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_score (
            qq        INTEGER NOT NULL,
            date      TEXT    NOT NULL,
            score     REAL    DEFAULT 0,
            sentences INTEGER DEFAULT 0,
            PRIMARY KEY (qq, date)
        )
        """
    )

    c.execute(
        """
        CREATE TABLE IF NOT EXISTS month_score (
            qq      INTEGER NOT NULL,
            month   TEXT    NOT NULL,
            total   REAL    DEFAULT 0,
            rank    INTEGER DEFAULT 0,
            robux   REAL    DEFAULT 0,
            carry   REAL    DEFAULT 0,
            bonus   REAL    DEFAULT 0,
            settled INTEGER DEFAULT 0,
            PRIMARY KEY (qq, month)
        )
        """
    )
    added_robux = _ensure_column(c, "month_score", "robux", "robux REAL DEFAULT 0")
    added_carry = _ensure_column(c, "month_score", "carry", "carry REAL DEFAULT 0")
    added_bonus = _ensure_column(c, "month_score", "bonus", "bonus REAL DEFAULT 0")
    _ensure_column(c, "month_score", "settled", "settled INTEGER DEFAULT 0")

    # 无损迁移: 旧版把上月结转/邀请分直接混进未结算行的 total，
    # 新逻辑要求 total 只存结算结果，结转走 carry、邀请分走 bonus。
    if added_carry or added_bonus or added_robux:
        c.execute(
            """
            UPDATE month_score
            SET carry = COALESCE(total, 0), total = 0
            WHERE settled = 0 AND rank = 0
              AND COALESCE(total, 0) > 0
            """
        )

    c.execute(
        """
        CREATE TABLE IF NOT EXISTS join_log (
            qq         INTEGER PRIMARY KEY,
            group_id   INTEGER NOT NULL,
            invited_by INTEGER DEFAULT 0,
            join_date  TEXT    NOT NULL
        )
        """
    )
    # 邀请人来源: 0=不知道(成员同步补录的老成员/主动入群) 1=入群事件检测到
    # 2=邀请人自己在时限内绑定确认的 3=管理员在后台带两个参数代填的
    _ensure_column(
        c, "join_log", "invited_src", "invited_src INTEGER DEFAULT 0"
    )
    # OneBot 的入群方式: invite=有人拉 / approve=管理员审批 / link=链接或搜索入群。
    # approve 时接口回的 operator_id 是点同意的管理员而不是拉人的人，所以单独存一份。
    _ensure_column(c, "join_log", "join_kind", "join_kind TEXT")
    # 入群时刻（epoch 秒）。join_date 只到日，老成员的时间靠成员同步从 NapCat 的
    # join_time 回填这一列，插件没在线时进的人也能对上准确的入群时间。
    _ensure_column(c, "join_log", "join_ts", "join_ts INTEGER DEFAULT 0")

    # 待确认的邀请关系：只由入群事件写入（有精确到秒的入群时刻才能算时限），
    # 成员同步不写它。绑定成功或超时都不再生效。
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS pending_bind (
            newbie_qq        INTEGER PRIMARY KEY,
            group_id         INTEGER NOT NULL,
            detected_inviter INTEGER DEFAULT 0,
            join_ts          INTEGER NOT NULL,
            bound_ts         INTEGER DEFAULT 0
        )
        """
    )
    # 老库的入群记录没有来源，能查到邀请人的一律按"事件检测到"处理
    if c.execute(
        "SELECT COUNT(*) FROM join_log WHERE COALESCE(invited_src,0)=0 "
        "AND COALESCE(invited_by,0)!=0"
    ).fetchone()[0]:
        c.execute(
            "UPDATE join_log SET invited_src=1 "
            "WHERE COALESCE(invited_src,0)=0 AND COALESCE(invited_by,0)!=0"
        )

    c.execute(
        """
        CREATE TABLE IF NOT EXISTS members (
            qq        INTEGER NOT NULL,
            group_id  INTEGER NOT NULL,
            nickname  TEXT,
            join_date TEXT,
            role      TEXT,
            in_group  INTEGER DEFAULT 1,
            first_seen TEXT,
            last_sync TEXT,
            PRIMARY KEY (qq, group_id)
        )
        """
    )
    _ensure_column(c, "members", "in_group", "in_group INTEGER DEFAULT 1")
    # NapCat 群成员列表带的 join_time（epoch 秒）。插件装之前就进群的人，
    # 入群时刻只能由每小时的成员同步从这一列补回来。
    _ensure_column(c, "members", "join_ts", "join_ts INTEGER DEFAULT 0")
    # NapCat 群成员列表的 join_time（epoch 秒），插件装之前就进群的也拿得到
    _ensure_column(c, "members", "join_ts", "join_ts INTEGER DEFAULT 0")

    # 入群事件流水（只追加）。同一 qq 出现 >=2 条即"退了又进"，永远不算新人。
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS join_history (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            qq         INTEGER NOT NULL,
            group_id   INTEGER NOT NULL,
            join_date  TEXT    NOT NULL,
            invited_by INTEGER DEFAULT 0
        )
        """
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_join_history_qq ON join_history(qq)"
    )
    # 迁移: 用旧 join_log 的每一条作为"首次入群事件"补进流水（只补一次）
    if c.execute("SELECT COUNT(*) FROM join_history").fetchone()[0] == 0:
        c.execute(
            """
            INSERT INTO join_history(qq, group_id, join_date, invited_by)
            SELECT qq, group_id, join_date, COALESCE(invited_by, 0) FROM join_log
            """
        )

    c.execute(
        """
        CREATE TABLE IF NOT EXISTS bind (
            qq        INTEGER PRIMARY KEY,
            roblox_id TEXT    NOT NULL UNIQUE
        )
        """
    )

    c.execute(
        """
        CREATE TABLE IF NOT EXISTS award_history (
            qq    INTEGER NOT NULL,
            month TEXT    NOT NULL,
            PRIMARY KEY (qq, month)
        )
        """
    )

    # 手工账目调整（/扣除 写负分，也可写正分补分）。按月记账，独立于 daily_score，
    # 所以 /重置日 /重置月 /补跑 重算日分都不会把它抹掉。
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS score_adjust (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            qq      INTEGER NOT NULL,
            month   TEXT    NOT NULL,
            delta   REAL    NOT NULL,
            reason  TEXT,
            created TEXT    NOT NULL
        )
        """
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_score_adjust_qq_month "
        "ON score_adjust(qq, month)"
    )

    c.execute(
        """
        CREATE TABLE IF NOT EXISTS newbie_bonus (
            newbie_qq     INTEGER NOT NULL,
            inviter_qq    INTEGER NOT NULL,
            awarded_month TEXT    NOT NULL,
            bonus         INTEGER DEFAULT 10,
            PRIMARY KEY (newbie_qq)
        )
        """
    )
    # 旧库 PK 是 (newbie_qq, awarded_month)，一个人只奖一次，
    # 用唯一索引兜底防重复发奖。
    c.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_newbie_qq ON newbie_bonus(newbie_qq)"
    )

    # 邀请关系的人工确认窗口：入群事件开一条，绑定成功写 bound_ts，过期的由启动/同步清理。
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS pending_bind (
            newbie_qq        INTEGER PRIMARY KEY,
            group_id         INTEGER NOT NULL,
            detected_inviter INTEGER DEFAULT 0,
            join_ts          INTEGER NOT NULL,
            bound_ts         INTEGER DEFAULT 0
        )
        """
    )

    c.execute(
        """
        CREATE TABLE IF NOT EXISTS user_info (
            qq   INTEGER PRIMARY KEY,
            name TEXT
        )
        """
    )
    _ensure_column(c, "user_info", "name", "name TEXT")
    _ensure_column(c, "user_info", "nickname", "nickname TEXT")

    conn.commit()
    conn.close()
    print(f"[init_db] 数据库已初始化: {path}")
    return path


if __name__ == "__main__":
    init()
