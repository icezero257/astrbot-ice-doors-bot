"""
doors_bot - 群消息入库与入群记录

计分口径说明:
  msg 表记录的是"候选有效发言"，入库时依次过滤:
    1) 纯文本消息（图片/表情/@/指令不计）
    2) 无意义消息: 单字符或纯数字（防 1/2/3 接龙刷屏）
    3) 同人 5 秒连发去重
    4) 与该人上一条内容完全相同 → 不计（防复读刷屏）
    5) 轮次合并: 同一人连续一串消息且中间无他人插话，
       间隔 < turn_gap_seconds(默认60s) 的全部合并算 1 条；
       有他人插话后恢复正常的逐条计数(仍受 5 秒去重约束)
  是否真正计分(120秒窗口内 >=2 人说话)在计分/查榜时按窗口判定，
  这样第二个人开口后，第一个人此前的消息也能追溯计分。
"""

from __future__ import annotations

import datetime
import sqlite3
import time

from astrbot.api import logger
from astrbot.api import message_components as _components

Plain = _components.Plain
# AstrBot <=4.25 的组件库里没有 Source，兼容取之
_Source = getattr(_components, "Source", None)
TEXT_ONLY_TYPES = (Plain, _Source) if _Source is not None else (Plain,)

from .runtime_config import (
    get_daily_config,
    get_db_path,
    get_group_id,
    get_invite_bind_config,
    get_no_score_qqs,
)


def get_db():
    return sqlite3.connect(get_db_path())


def get_today() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d")


def is_valid_text(text: str) -> bool:
    return bool(text and text.strip())


def is_meaningless(text: str) -> bool:
    """单字符或纯数字消息视为无意义（防 1/2/3 接龙式刷屏）。"""
    s = text.strip()
    return len(s) <= 1 or (s.isascii() and s.isdigit())


def is_pure_text_message(event) -> bool:
    """仅纯文本消息计分：图片/表情/视频/文件/@/回复卡片等一律不计。引用头(Source)允许。"""
    try:
        components = list(event.get_messages() or [])
    except Exception:
        return False
    if not components:
        return False
    return all(isinstance(c, TEXT_ONLY_TYPES) for c in components)


def _upsert_user(cursor: sqlite3.Cursor, qq: int, name: str) -> None:
    """登记/更新昵称缓存。

    AstrBot 的 get_sender_name() 拿不到昵称时会退回 QQ 号，所以传进来的"昵称"
    有时就是那串数字（后台看到 昵称：111 就是这么来的）。占位值不许覆盖已经存到的
    真昵称，真昵称（成员同步/发言事件）随时覆盖。
    """
    cursor.execute(
        """
        INSERT INTO user_info (qq, name)
        VALUES (?, ?)
        ON CONFLICT(qq) DO UPDATE SET name=excluded.name
        WHERE excluded.name <> CAST(excluded.qq AS TEXT)
           OR COALESCE(user_info.name, '') IN ('', CAST(user_info.qq AS TEXT))
        """,
        (qq, name),
    )


_SELF_ID: int | None = None


def remember_self_id(event) -> None:
    """记下机器人自己的 QQ 号。

    后台任务(定时同步)不在事件上下文里，aiocqhttp 只认 call_action 带上 self_id
    才能选中那条反向 WS 连接，否则抛 ApiNotAvailable(而且 str() 是空的)。
    """
    global _SELF_ID
    sid = getattr(event, "get_self_id", lambda: None)()
    if sid:
        try:
            _SELF_ID = int(sid)
        except (TypeError, ValueError):
            pass


def get_self_id() -> int | None:
    return _SELF_ID


async def handle_group_message(event, _now: int | None = None) -> None:
    remember_self_id(event)
    group_id = event.get_group_id()
    if not group_id:
        return

    target_group = get_group_id()
    if not target_group or int(group_id) != target_group:
        return

    # 指令消息、@机器人的消息不计入发言
    if getattr(event, "is_at_or_wake_command", False):
        return

    text = event.message_str
    if not is_valid_text(text):
        return

    if not is_pure_text_message(event):
        return

    qq = int(event.get_sender_id())
    if qq == 0 or str(qq) == str(event.get_self_id()):
        return

    if qq in get_no_score_qqs():
        return  # 配置的免计分QQ（群内机器人）：完全不入库

    nickname = None
    try:
        sender = event.get_sender()
        if sender:
            nickname = (
                getattr(sender, "card", None)
                or getattr(sender, "nickname", None)
                or getattr(sender, "name", None)
            )
    except Exception:
        pass
    if not nickname:
        nickname = event.get_sender_name() or str(qq)

    content = text.strip()
    now = int(_now if _now is not None else time.time())
    daily_cfg = get_daily_config()

    if daily_cfg["meaningless_filter"] and is_meaningless(content):
        return

    dedup_seconds = daily_cfg["dedup_seconds"]
    turn_gap = daily_cfg["turn_gap_seconds"]
    content_dedup = daily_cfg["content_dedup"]

    conn = get_db()
    try:
        cursor = conn.cursor()
        last = cursor.execute(
            """
            SELECT ts, content
            FROM msg
            WHERE qq=? AND group_id=?
            ORDER BY ts DESC LIMIT 1
            """,
            (qq, target_group),
        ).fetchone()
        if last:
            last_ts, last_content = last[0], (last[1] or "")
            if now - last_ts < dedup_seconds:
                return
            if content_dedup and last_content == content:
                return
            # 无他人插话的连发：turn_gap 秒内合并算一条
            other_ts = cursor.execute(
                "SELECT MAX(ts) FROM msg WHERE group_id=? AND qq<>?",
                (target_group, qq),
            ).fetchone()[0]
            if (other_ts is None or other_ts <= last_ts) and now - last_ts < turn_gap:
                return

        cursor.execute(
            """
            INSERT INTO msg (qq, group_id, date, ts, content)
            VALUES (?, ?, ?, ?, ?)
            """,
            (qq, target_group, get_today(), now, content),
        )
        _upsert_user(cursor, qq, str(nickname))
        conn.commit()
        logger.info(f"[doors_bot] 记录消息 QQ:{qq}")
    finally:
        conn.close()


async def handle_group_join(event, _now: int | None = None) -> dict | None:
    """处理 NapCat 转发的群入群通知 (group_increase notice)。

    返回 None 表示这条通知不归我们处理。开出了人工确认窗口、且配置页允许在群里
    催一句时，返回 {"newbie": 新人QQ, "inviter": 入群事件里带的那个QQ,
    "approve": 这次是不是管理员放行}，由调用方决定怎么问。
    """
    raw = getattr(event.message_obj, "raw_message", None)
    if not isinstance(raw, dict):
        return
    if raw.get("post_type") != "notice":
        return
    if raw.get("notice_type") != "group_increase":
        return

    group_id = raw.get("group_id") or event.get_group_id()
    if not group_id:
        return

    target_group = get_group_id()
    if not target_group or int(group_id) != target_group:
        return

    user_id = int(raw.get("user_id") or event.get_sender_id() or 0)
    if not user_id:
        return

    invited_by = int(raw.get("operator_id") or 0)
    if invited_by == user_id:
        # 主动入群(群号/二维码)时 operator_id 常常就是本人，不能算自己邀请自己
        invited_by = 0
    # invite=有人拉 / approve=管理员审批(此时 operator_id 是审批人) / link=搜群号进来
    join_kind = str(raw.get("sub_type") or "") or ""
    join_date = datetime.date.today().strftime("%Y-%m-%d")
    join_ts = int(_now if _now is not None else time.time())

    conn = get_db()
    try:
        cursor = conn.cursor()

        # 新人判定防"退了又进"卡加成:
        #   join_history 每人只应有 1 条(首次入群)。若本次入群前已有成员痕迹
        #   (join_log / members / 历史流水)但流水缺失，则补记 2 条使其 count>=2，
        #   永久失去新人与邀请奖励资格。
        hist_cnt = cursor.execute(
            "SELECT COUNT(*) FROM join_history WHERE qq=?", (user_id,)
        ).fetchone()[0]
        known_before = cursor.execute(
            "SELECT 1 FROM join_log WHERE qq=?", (user_id,)
        ).fetchone() or cursor.execute(
            "SELECT 1 FROM members WHERE qq=? AND group_id=?",
            (user_id, target_group),
        ).fetchone()

        if hist_cnt == 0 and known_before:
            # 机器人上线前的旧 spell + 本次重进，记两条
            cursor.execute(
                """
                INSERT INTO join_history(qq, group_id, join_date, invited_by)
                VALUES(?,?,?,0)
                """,
                (user_id, target_group, "1970-01-01"),
            )
        cursor.execute(
            """
            INSERT INTO join_history(qq, group_id, join_date, invited_by)
            VALUES(?,?,?,?)
            """,
            (user_id, target_group, join_date, invited_by),
        )

        cursor.execute(
            """
            INSERT INTO join_log (qq, group_id, invited_by, join_date,
                                  invited_src, join_kind, join_ts)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(qq) DO UPDATE SET
                group_id=excluded.group_id,
                invited_by=CASE
                    WHEN excluded.invited_by != 0 THEN excluded.invited_by
                    ELSE join_log.invited_by
                END,
                invited_src=CASE
                    WHEN excluded.invited_by != 0 THEN excluded.invited_src
                    ELSE join_log.invited_src
                END,
                join_kind=excluded.join_kind,
                join_date=excluded.join_date,
                join_ts=excluded.join_ts
            """,
            (user_id, target_group, invited_by, join_date,
             1 if invited_by else 0, join_kind, join_ts),
        )
        bind_cfg = get_invite_bind_config()
        if bind_cfg["enabled"]:
            # 开一个确认窗口：这次看到谁拉的人只是"检测"，邀请人在窗口内自己发指令
            # 绑定才算人工确认。只记最新一次入群，绑定成功或超时后不再有效。
            # 审批放行那次的 operator_id 是管理员、不是拉人的人，所以窗口里当成"没检测到"，
            # 等真正的邀请人来认领；join_log 仍留着当时是谁放行的，便于事后核对。
            cursor.execute(
                """
                INSERT INTO pending_bind
                    (newbie_qq, group_id, detected_inviter, join_ts, bound_ts)
                VALUES(?,?,?,?,0)
                ON CONFLICT(newbie_qq) DO UPDATE SET
                    group_id=excluded.group_id,
                    detected_inviter=excluded.detected_inviter,
                    join_ts=excluded.join_ts,
                    bound_ts=0
                """,
                (user_id, target_group,
                 0 if join_kind == "approve" else invited_by, join_ts),
            )
            cursor.execute(
                "DELETE FROM pending_bind WHERE join_ts < ? AND bound_ts=0",
                (join_ts - 86400,),
            )
        # 这里不写 user_info：通知事件里适配器把 sender.nickname 填成 user_id 本身，
        # 写进去只会让榜单显示成一串数字，真昵称由成员同步与后续发言补齐。
        conn.commit()
        logger.info(
            f"[doors_bot] 新成员加入 {user_id} "
            + (
                f"审批人 {invited_by}(不算邀请人)"
                if join_kind == "approve"
                else f"邀请人 {invited_by or '未检测到'}"
            )
            + f" 方式 {raw.get('sub_type') or '-'}"
        )
        # 只有知道该问谁(检测到 operator_id)时才提示，链接/搜索入群没有对象可问。
        if bind_cfg["enabled"] and bind_cfg["prompt"] and invited_by:
            return {
                "newbie": user_id,
                "inviter": invited_by,
                "approve": join_kind == "approve",
            }
    finally:
        conn.close()
