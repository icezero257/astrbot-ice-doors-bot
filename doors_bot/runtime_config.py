"""
doors_bot - 运行时配置统一入口
主数据源: AstrBot 插件配置页 (_conf_schema.json)

数据库路径不再受 .env 影响: .env 的 DB_PATH 常写成 ./doors.db 这种相对路径，
而 load_dotenv() 找 .env 又依赖启动时的工作目录，结果库会在插件目录里被凭空
建出来，删/覆盖插件时数据一起消失。现在路径只由 配置页 db_path + 固定数据目录
决定，且数据目录在插件目录之外。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent


def _deployed_root() -> Path | None:
    """部署形态(<AstrBot根>/data/plugins/doors_bot)时返回 <AstrBot根>，否则 None。"""
    if len(BASE_DIR.parents) > 2 and BASE_DIR.parents[0].name == "plugins":
        return BASE_DIR.parents[2]
    return None


def _default_data_dir() -> Path:
    """默认数据目录：与插件目录同级，但不在插件目录里面。

    部署形态 → <AstrBot根>/data/plugins/doors_bot_data
      AstrBot 卸载/更新插件只操作 data/plugins/<插件名> 这一个目录，
      勾"同时删除数据"删的是 data/plugin_data/<插件名>，两者都碰不到同级的
      doors_bot_data。手工清空整个 plugins 目录前请先备份库。
    开发副本(不在 plugins 下) → 插件目录同级的 doors_bot_data。
    """
    root = _deployed_root()
    if root is not None:
        return root / "data" / "plugins" / "doors_bot_data"
    return BASE_DIR.parent / "doors_bot_data"


# v2.4.0 的默认位置：<AstrBot根> 的父目录/doors_bot_data，仅用于自检提示遗留库
def _prev_default_db() -> Path | None:
    root = _deployed_root()
    if root is None:
        return None
    parent = root.parent
    return (parent if parent != root else root) / "doors_bot_data" / "doors.db"


def get_data_dir() -> Path:
    """榜单库与发放名单的存放目录，可用 DOORS_BOT_DATA_DIR 或配置页 db_path 改写。"""
    env_dir = os.getenv("DOORS_BOT_DATA_DIR", "").strip()
    if env_dir:
        return Path(env_dir).expanduser().resolve()
    return _default_data_dir()


def get_db_file() -> Path:
    return get_data_dir() / "doors.db"


def ensure_data_dir() -> Path:
    data_dir = get_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


# 旧版默认库位置(AstrBot 根目录)，仅用于自检时提示迁移
DEFAULT_DB_PATH = (BASE_DIR / ".." / ".." / ".." / "doors.db").resolve()

_config: dict[str, Any] = {}


def set_plugin_config(config: dict[str, Any] | None) -> None:
    global _config
    _config = dict(config or {})


def get_plugin_config() -> dict[str, Any]:
    return _config


def _section(name: str) -> dict[str, Any]:
    value = _config.get(name, {})
    return value if isinstance(value, dict) else {}


def _to_int(raw: Any, default: int = 0) -> int:
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return default


def _to_float(raw: Any, default: float = 0.0) -> float:
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError):
        return default


def _to_bool(raw: Any, default: bool = False) -> bool:
    if isinstance(raw, bool):
        return raw
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def get_db_path() -> str:
    """库路径只由 配置页 db_path + 数据目录 决定，与启动目录无关。

    配置页填相对路径时按数据目录解析(而不是按插件目录)，避免把库写进插件目录。
    """
    configured = str(_config.get("db_path") or "").strip()
    if configured:
        path = Path(configured).expanduser()
        if not path.is_absolute():
            path = ensure_data_dir() / configured
        target = str(path)
    else:
        target = str(ensure_data_dir() / "doors.db")
    parent = os.path.dirname(os.path.abspath(target))
    if parent:
        os.makedirs(parent, exist_ok=True)
    return target


def get_pay_list_dir() -> Path:
    """发放名单与数据库同目录，删/覆盖插件不会带走记录。"""
    return Path(os.path.dirname(os.path.abspath(get_db_path())))


def get_group_id() -> int:
    raw = _config.get("group_id") or os.getenv("GROUP_ID", "0")
    return _to_int(raw, 0)


def _to_qq_set(raw: Any) -> set[int]:
    parts = str(raw).replace("，", ",").replace(",", " ").split()
    return {qq for qq in (_to_int(p, 0) for p in parts) if qq > 0}


def get_no_score_qqs() -> set[int]:
    """完全不参与计分的QQ列表（群内机器人）：发言不入库、不加分，也不吃未发言扣分。"""
    raw = _config.get("no_score_qqs")
    if raw is None:
        raw = os.getenv("NO_SCORE_QQS", "")
    return _to_qq_set(raw)


def legacy_db_candidates() -> list[Path]:
    """历史遗留的库位置，只读探测用于自检提示，绝不自动移动或删除。

    覆盖：插件目录内(旧 .env 相对路径)、AstrBot 根目录(v2.3 及更早默认)、
    v2.4.0 的 <AstrBot根同级>/doors_bot_data。
    """
    out = [BASE_DIR / "doors.db"]  # 被 .env 相对路径带偏时生成在插件目录里
    if DEFAULT_DB_PATH not in out:
        out.append(DEFAULT_DB_PATH)
    prev = _prev_default_db()
    if prev is not None and prev not in out:
        out.append(prev)
    current = Path(os.path.abspath(get_db_path()))
    return [p for p in out if p != current and p.exists()]


def get_daily_config() -> dict[str, Any]:
    section = _section("daily_score")
    return {
        "per_msg_score": _to_float(section.get("per_msg_score"), 0.05),
        "daily_cap": _to_float(section.get("daily_cap"), 3.0),
        "bonus_threshold": _to_int(section.get("bonus_threshold"), 10),
        "bonus_score": _to_float(section.get("bonus_score"), 1.0),
        "penalty": _to_float(section.get("penalty"), 0.2),
        "dedup_seconds": _to_int(section.get("dedup_seconds"), 5),
        "turn_gap_seconds": _to_int(section.get("turn_gap_seconds"), 60),
        "content_dedup": _to_bool(section.get("content_dedup"), True),
        "meaningless_filter": _to_bool(section.get("meaningless_filter"), True),
        "multi_speaker_window_seconds": _to_int(
            section.get("multi_speaker_window_seconds"), 120
        ),
        "multi_speaker_min_count": _to_int(
            section.get("multi_speaker_min_count"), 2
        ),
    }


def get_monthly_config() -> dict[str, Any]:
    section = _section("monthly_settle")
    invite = _section("invite_bonus")
    return {
        "top_n": _to_int(section.get("top_n"), 10),
        "award_per_top": _to_int(section.get("award_per_top"), 50),
        "newbie_multiplier": _to_float(section.get("newbie_multiplier"), 1.1),
        "decay_rate": _to_float(section.get("decay_rate"), 0.1),
        "carry_over": _to_float(section.get("carry_over"), 0.05),
        "invite_enabled": _to_bool(invite.get("enabled"), True),
        "invite_window_days": _to_int(invite.get("window_days"), 10),
        "invite_daily_min_msgs": _to_int(invite.get("daily_min_msgs"), 10),
        "invite_avg_daily_msgs": _to_int(invite.get("avg_daily_msgs"), 20),
        "invite_bonus": _to_int(invite.get("bonus"), 10),
    }


def get_report_config() -> dict[str, Any]:
    """display_limit <= 0 表示不限量；榜单一律不显示 0 分的行。"""
    section = _section("report")
    return {"display_limit": _to_int(section.get("display_limit"), 0)}


def get_scheduler_config() -> dict[str, Any]:
    section = _section("scheduler")
    return {
        "daily_job_hour": _to_int(section.get("daily_job_hour"), 23),
        "daily_job_minute": _to_int(section.get("daily_job_minute"), 59),
        "monthly_job_day": _to_int(section.get("monthly_job_day"), 1),
        "monthly_job_hour": _to_int(section.get("monthly_job_hour"), 9),
        "monthly_job_minute": _to_int(section.get("monthly_job_minute"), 0),
        "member_sync_minutes": _to_int(section.get("member_sync_minutes"), 60),
        "catchup_minutes": _to_int(section.get("catchup_minutes"), 30),
        "timezone": str(section.get("timezone") or "").strip(),
    }


def estimate_day_score(
    sentences: int, daily_cfg: dict[str, Any] | None = None
) -> float:
    cfg = daily_cfg or get_daily_config()
    sentence_score = min(sentences * cfg["per_msg_score"], cfg["daily_cap"])
    bonus = cfg["bonus_score"] if sentences >= cfg["bonus_threshold"] else 0.0
    return round(sentence_score + bonus, 2)
