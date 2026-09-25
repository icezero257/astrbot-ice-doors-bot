"""
doors_bot - 定时任务（纯后台，不发送任何消息）

- 每日(默认23:59): 计算当日得分 + 邀请奖励判定
- 每月1日(默认09:00): 结算上月，结果写数据库 month_score(含robux列)
  并导出 pay_list_YYYY-MM.txt
- 每N分钟(默认30): 补跑漏算的日期。23:59 任务只在进程活着时才会跑，
  机器关机/重启/AstrBot 未启动都会漏掉整天。补跑只处理"有 msg 但没有
  daily_score"的已过日期(机器人整天离线与全天没人说话无法区分，故不补扣分)。
- 每N分钟(默认60): 通过 NapCat 同步全群成员名单(含入群时间)，
  作为扣分名单与回流判定依据（只读接口，不发消息）

时区: 配置页 scheduler.timezone（留空跟随系统本地时区）。
"""

from __future__ import annotations

import datetime
import sqlite3
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from astrbot.api import logger

from .daily_score import calc_day
from .monthly_settle import settle
from .runtime_config import (
    get_daily_config,
    get_db_path,
    get_group_id,
    get_monthly_config,
    get_no_score_qqs,
    get_scheduler_config,
)

_context = None
_started = False

# 供"自检"指令读取的运行状态（只读展示，不参与计分）
STATE: dict[str, dict] = {
    "sync": {"last_attempt": "", "last_ok": "", "count": 0, "platform": "", "error": ""},
    "catchup": {"last_run": "", "recomputed": []},
}

_TZ: ZoneInfo | None = None
_CATCH_SEEN: set[str] = set()  # 本进程已尝试补算过的日期，防止零分日被反复重算


def _resolve_tz(raw: str) -> ZoneInfo | None:
    if not raw:
        return None
    try:
        return ZoneInfo(raw)
    except Exception:
        logger.warning(f"[doors_bot] 时区 {raw!r} 无效，改用系统本地时区")
        return None


def _scheduler() -> AsyncIOScheduler:
    global _TZ
    _TZ = _resolve_tz(get_scheduler_config()["timezone"])
    return AsyncIOScheduler(timezone=_TZ) if _TZ else AsyncIOScheduler()


scheduler = _scheduler()


def set_context(context) -> None:
    global _context
    _context = context


def _now() -> datetime.datetime:
    return datetime.datetime.now(_TZ) if _TZ else datetime.datetime.now()


def _today_str(now: datetime.date | None = None) -> str:
    return (now or _now()).strftime("%Y-%m-%d")


def _last_month(now: datetime.datetime | None = None) -> str:
    now = now or _now()
    if now.month == 1:
        return f"{now.year - 1}-12"
    return f"{now.year}-{now.month - 1:02d}"


def _conn() -> sqlite3.Connection:
    return sqlite3.connect(get_db_path())


def _month_settled(month: str) -> bool:
    conn = _conn()
    try:
        return bool(
            conn.execute(
                "SELECT 1 FROM month_score WHERE month=? AND settled=1 LIMIT 1",
                (month,),
            ).fetchone()
        )
    finally:
        conn.close()


def missing_calc_days(month: str, until_date: str) -> list[str]:
    """本月内已过去、有发言记录却没有日分的日期（23:59 任务漏跑的证据）。"""
    conn = _conn()
    try:
        with_msg = [
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT date FROM msg WHERE date LIKE ? AND date<? ORDER BY date",
                (f"{month}%", until_date),
            )
        ]
        done = {
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT date FROM daily_score WHERE date LIKE ?",
                (f"{month}%",),
            )
        }
    finally:
        conn.close()
    return [d for d in with_msg if d not in done]


def catchup_months(now: datetime.date | None = None) -> list[str]:
    """需要补跑的月份：本月 + 尚未结算的上月。"""
    now = now or _now()
    months = [now.strftime("%Y-%m")]
    last = _last_month(now)
    if not _month_settled(last):
        months.append(last)
    return months


def pending_catchup_days(now: datetime.date | None = None) -> list[str]:
    """本轮真正要补的日期。已尝试过的日期先跳过：整天无人有效发言时
    按定义永远"有 msg 无日分"，不记台账就会每半小时重算一遍。"""
    today = _today_str(now)
    days = sorted(
        {d for m in catchup_months(now) for d in missing_calc_days(m, today)}
    )
    return [d for d in days if d not in _CATCH_SEEN]


def run_catchup(
    force_days: list[str] | None = None, now: datetime.date | None = None
) -> list[str]:
    """补算指定日期，或所有"有 msg 无日分"的已过日期。返回实际补算的日期。

    force_days 用于 /重置日 /重置月 /删榜项 /结算 等自己算好日期的路径。
    """
    cfg = get_daily_config()
    monthly_cfg = get_monthly_config()
    group_id = get_group_id()
    exclude = get_no_score_qqs()
    days = force_days if force_days is not None else pending_catchup_days(now)
    _CATCH_SEEN.update(days)
    done: list[str] = []
    for day in days:
        try:
            calc_day(day, cfg, monthly_cfg, group_id, exclude)
            done.append(day)
        except Exception:
            logger.exception(f"[doors_bot] 补跑 {day} 失败")
    STATE["catchup"]["last_run"] = _now().strftime("%Y-%m-%d %H:%M:%S")
    STATE["catchup"]["recomputed"] = done
    if done:
        logger.info(f"[doors_bot] 补跑完成: {', '.join(done)}")
    return done


async def daily_job() -> None:
    cfg = get_daily_config()
    monthly_cfg = get_monthly_config()
    group_id = get_group_id()
    try:
        calc_day(_today_str(), cfg, monthly_cfg, group_id, get_no_score_qqs())
        logger.info("[doors_bot] 每日计分完成")
    except Exception:
        logger.exception("[doors_bot] 每日计分失败")


async def monthly_job() -> None:
    last_month = _last_month()
    try:
        run_catchup()  # 结算前先把上月漏算的日子补齐，避免月榜少分
        summary = settle(last_month, get_monthly_config())
        logger.info(
            f"[doors_bot] 月度结算完成: {last_month} "
            f"{summary['participants']}人参与 {summary['winners']}人获奖 "
            f"名单: {summary['pay_list_path']}"
        )
    except Exception:
        logger.exception("[doors_bot] 月度结算失败")


async def catchup_job() -> None:
    try:
        run_catchup()
    except Exception:
        logger.exception("[doors_bot] 补跑任务异常")


def _platform_candidates() -> list[tuple[str, object]]:
    """找出所有能调用 OneBot 只读接口的平台实例（NapCat/Official-Lagrange 等）。"""
    if _context is None:
        return []
    out = []
    try:
        insts = list(_context.platform_manager.platform_insts)
    except Exception:
        return []
    for platform in insts:
        try:
            meta_id = str(platform.meta().id)
        except Exception:
            meta_id = type(platform).__name__
        call_action = getattr(getattr(platform, "bot", None), "call_action", None)
        if call_action is not None:
            out.append((meta_id, call_action))
    return out


async def member_sync_job(now: datetime.datetime | None = None) -> None:
    """拉取全群成员，写入 members 表（扣分名单与新人判定依据）。

    now 仅供测试注入；生产留空取当前时间。
    """
    group_id = get_group_id()
    if not group_id:
        logger.warning("[doors_bot] 未配置 group_id，跳过群成员同步")
        return

    now_str = (now or _now()).strftime("%Y-%m-%d %H:%M:%S")
    state = STATE["sync"]
    state["last_attempt"] = now_str

    candidates = _platform_candidates()
    if not candidates:
        state["error"] = "无可调用 call_action 的平台"
        logger.warning(
            "[doors_bot] 群成员同步跳过: 当前没有可用的 OneBot 平台实例"
            "（只支持 NapCat / Official Account 等带 call_action 的适配器）"
        )
        return

    members = None
    errors = []
    for meta_id, call_action in candidates:
        for kwargs in ({}, {"no_cache": True}):
            try:
                result = await call_action(
                    "get_group_member_list", group_id=group_id, **kwargs
                )
            except Exception as e:
                errors.append(f"{meta_id}: {e}")
                continue
            if isinstance(result, list) and result:
                members, state["platform"] = result, meta_id
                break
        if members:
            break
    if not members:
        state["error"] = "; ".join(errors)[-300:] or "返回空名单"
        logger.warning(
            f"[doors_bot] 群成员同步失败(不写库，扣分名单保持上一次结果): {state['error']}"
        )
        return

    from .message_handler import _upsert_user

    conn = _conn()
    try:
        c = conn.cursor()
        for m in members:
            try:
                qq = int(m.get("user_id") or 0)
            except (TypeError, ValueError):
                continue
            if qq <= 0:
                continue
            join_date = None
            join_ts = m.get("join_time") or 0
            try:
                if int(join_ts) > 0:
                    join_date = datetime.datetime.fromtimestamp(
                        int(join_ts), _TZ or datetime.datetime.now().astimezone().tzinfo
                    ).strftime("%Y-%m-%d")
            except (TypeError, ValueError, OSError):
                join_date = None
            nickname = str(m.get("card") or m.get("nickname") or "") or None
            role = str(m.get("role") or "")
            c.execute(
                """
                INSERT INTO members(qq, group_id, nickname, join_date, role,
                                    first_seen, last_sync)
                VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(qq, group_id) DO UPDATE SET
                    nickname=COALESCE(excluded.nickname, members.nickname),
                    join_date=COALESCE(excluded.join_date, members.join_date),
                    role=excluded.role,
                    in_group=1,
                    last_sync=excluded.last_sync
                """,
                (qq, group_id, nickname, join_date, role, now_str, now_str),
            )
            if nickname:
                _upsert_user(c, qq, nickname)
            # 插件安装前就入群的人没有入群事件，回填 join_log：
            # 否则他们既进不了扣分名单，也拿不到新人系数（invited_by=0 不触发邀请奖励）
            if join_date and not c.execute(
                "SELECT 1 FROM join_log WHERE qq=?", (qq,)
            ).fetchone():
                c.execute(
                    "INSERT INTO join_log(qq, group_id, invited_by, join_date) "
                    "VALUES(?,?,0,?)",
                    (qq, group_id, join_date),
                )
        # 本次名单里没有的人 → 已退群，标记 in_group=0（停止扣分、防回流误判）
        c.execute(
            "UPDATE members SET in_group=0 WHERE group_id=? AND last_sync < ?",
            (group_id, now_str),
        )
        conn.commit()
    finally:
        conn.close()
    state["last_ok"] = now_str
    state["count"] = len(members)
    state["error"] = ""
    logger.info(f"[doors_bot] 群成员同步完成: {len(members)} 人 (平台 {state['platform']})")


def schedule_info() -> list[str]:
    """自检用：已登记任务与下次触发时间（时区可见）。"""
    lines = []
    for job in scheduler.get_jobs():
        nrt = getattr(job, "next_run_time", None)
        lines.append(f"{job.id}: 下次 {nrt.strftime('%Y-%m-%d %H:%M:%S %Z') if nrt else '未运行'}")
    return lines


def start() -> None:
    global _started, scheduler
    if _started:
        return
    sched_cfg = get_scheduler_config()
    if not scheduler.running:
        scheduler = _scheduler()
    scheduler.add_job(
        daily_job,
        "cron",
        hour=sched_cfg["daily_job_hour"],
        minute=sched_cfg["daily_job_minute"],
        id="doors_daily_job",
    )
    scheduler.add_job(
        monthly_job,
        "cron",
        day=sched_cfg["monthly_job_day"],
        hour=sched_cfg["monthly_job_hour"],
        minute=sched_cfg["monthly_job_minute"],
        id="doors_monthly_job",
    )
    scheduler.add_job(
        member_sync_job,
        "interval",
        minutes=max(1, sched_cfg["member_sync_minutes"]),
        id="doors_member_sync",
    )
    scheduler.add_job(
        catchup_job,
        "interval",
        minutes=max(5, sched_cfg["catchup_minutes"]),
        id="doors_catchup_job",
    )
    scheduler.start()
    _started = True
    tz_name = str(_TZ) if _TZ else "系统本地时区"
    logger.info(f"[doors_bot] 定时任务已启动 (时区 {tz_name})")
    for line in schedule_info():
        logger.info(f"[doors_bot]   {line}")


def shutdown() -> None:
    global _started
    if _started and scheduler.running:
        scheduler.shutdown(wait=False)
    _started = False
