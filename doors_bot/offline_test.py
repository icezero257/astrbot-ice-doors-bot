"""
doors_bot 离线测试（不需要真实 AstrBot，stub 掉 astrbot 模块）

运行: 在 doors_bot 的父目录执行  python doors_bot/offline_test.py
      或                        python offline_test.py (自动加父目录到 sys.path)
"""

from __future__ import annotations

import asyncio
import datetime
import os
import sqlite3
import sys
import tempfile
import time
import types

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PARENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PARENT not in sys.path:
    sys.path.insert(0, PARENT)

# ---------------- astrbot stubs ----------------

def _stub_module(name: str, **attrs):
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    return mod


class _Logger:
    def info(self, *a):
        pass

    def warning(self, *a):
        pass

    def debug(self, *a):
        pass

    def exception(self, *a):
        pass

    def critical(self, *a):
        pass


class Plain:
    def __init__(self, text=""):
        self.text = text


class Source:
    pass


class Image:
    pass


class At:
    pass


def _passthrough_decorator_factory(*a, **k):
    def deco(fn):
        return fn

    return deco


class EventMessageType:
    GROUP_MESSAGE = 1
    PRIVATE_MESSAGE = 2
    OTHER_MESSAGE = 3
    ALL = 7


class PlatformAdapterType:
    WEBCHAT = "webchat"
    AIOCQHTTP = "aiocqhttp"
    ALL = "all"


class _Filter:
    event_message_type = staticmethod(_passthrough_decorator_factory)
    command = staticmethod(_passthrough_decorator_factory)
    platform_adapter_type = staticmethod(_passthrough_decorator_factory)
    EventMessageType = EventMessageType
    PlatformAdapterType = PlatformAdapterType


class Star:
    def __init__(self, context=None):
        self.context = context


_stub_module("astrbot")
_stub_module("astrbot.api", logger=_Logger(), html_renderer=None)
_stub_module("astrbot.api.event", filter=_Filter(), AstrMessageEvent=object,
             MessageChain=list)
_stub_module("astrbot.api.message_components", Plain=Plain, Source=Source,
             Image=Image, At=At)
_stub_module("astrbot.api.star", Star=Star,
             register=_passthrough_decorator_factory)

from doors_bot import daily_score as ds  # noqa: E402
from doors_bot import monthly_settle as ms  # noqa: E402
from doors_bot.init_db import init  # noqa: E402
from doors_bot import message_handler as mh  # noqa: E402
from doors_bot.runtime_config import (  # noqa: E402
    set_plugin_config,
    get_daily_config,
    get_monthly_config,
    get_db_path,
)

GROUP = 100
PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f" FAIL {name}  {detail}")


def make_db(tag: str) -> str:
    path = os.path.join(TMP, f"{tag}.db")
    set_plugin_config(
        {
            "db_path": path,
            "group_id": str(GROUP),
            "invite_bonus": {"enabled": True, "window_days": 10,
                             "daily_min_msgs": 10, "avg_daily_msgs": 20,
                             "bonus": 10},
        }
    )
    init(path)
    return path


def add_msg(conn, qq, date_str, ts):
    conn.execute(
        "INSERT INTO msg(qq, group_id, date, ts) VALUES(?,?,?,?)",
        (qq, GROUP, date_str, int(ts)),
    )


TMP = tempfile.mkdtemp(prefix="doors_test_")
DCFG = get_daily_config()
MCfg = get_monthly_config()

# ================= DB1: 入库 / 去重 / 纯文本 / 入群事件 =================
print("== DB1: message ingest, dedup, pure-text, join events ==")
p1 = make_db("db1")
conn1 = sqlite3.connect(p1)


class FakeSender:
    def __init__(self, uid, name="nick"):
        self.user_id = uid
        self.card = name
        self.nickname = name


class FakeMsgObj:
    def __init__(self, raw=None, sender=None):
        self.raw_message = raw or {}
        self.sender = sender


class FakeEvent:
    def __init__(self, uid, text, comps=None, raw=None, gid=GROUP, is_cmd=False):
        self._uid = uid
        self.message_str = text
        self._comps = comps if comps is not None else [Plain(text)]
        self.message_obj = FakeMsgObj(raw=raw)
        self._gid = gid
        self.is_at_or_wake_command = is_cmd

    def get_group_id(self):
        return str(self._gid) if self._gid else ""

    def get_sender_id(self):
        return str(self._uid)

    def get_self_id(self):
        return "9999"

    def get_sender(self):
        return FakeSender(self._uid)

    def get_sender_name(self):
        return "nick"

    def get_messages(self):
        return self._comps

    def is_admin(self):
        return False


T0 = int(time.time())


async def msg(qq, text, off, comps=None, is_cmd=False):
    await mh.handle_group_message(
        FakeEvent(qq, text, comps=comps, is_cmd=is_cmd), _now=T0 + off
    )


def n_msgs(qq):
    return conn1.execute(
        "SELECT COUNT(*) FROM msg WHERE qq=?", (qq,)
    ).fetchone()[0]


async def db1():
    await msg(1001, "hello", 0)
    n = conn1.execute("SELECT COUNT(*) FROM msg").fetchone()[0]
    check("记录一条消息", n == 1, n)

    await msg(1001, "hello2", 1)
    check("5秒内同人第二条不记录", n_msgs(1001) == 1, n_msgs(1001))

    # 纯文本判定: 带图片不记录
    await msg(1002, "看图看图", 2, comps=[Plain("看图看图"), Image()])
    check("含图片消息不记录", n_msgs(1002) == 0, n_msgs(1002))

    # @ 消息不记录
    await msg(1003, "hihi", 3, comps=[Plain("hihi"), At()])
    check("含@消息不记录", n_msgs(1003) == 0, n_msgs(1003))

    # 指令消息不记录
    await msg(1004, "/发言日榜", 4, is_cmd=True)
    check("指令消息不计发言", n_msgs(1004) == 0, n_msgs(1004))

    # 空文本
    await msg(1005, "   ", 5)
    check("空白消息不记录", n_msgs(1005) == 0, n_msgs(1005))

    # 昵称缓存
    name = conn1.execute(
        "SELECT name FROM user_info WHERE qq=1001"
    ).fetchone()
    check("昵称登记", name and name[0] == "nick", name)

    # 轮次合并: 无他人插话时 60 秒内连发全算一条
    await msg(1001, "later", 10)
    check("无插话60秒内连发合并算1条", n_msgs(1001) == 1, n_msgs(1001))

    await msg(1002, "在吗在吗", 11)
    check("他人第一条正常记录", n_msgs(1002) == 1, n_msgs(1002))

    await msg(1001, "later2", 12)
    check("有他人插话后恢复计数", n_msgs(1001) == 2, n_msgs(1001))

    # 内容去重: 与自己上一条完全相同 → 不计(即使中间有他人)
    await msg(1002, "在吗在吗", 20)
    check("与上一条内容相同不记录", n_msgs(1002) == 1, n_msgs(1002))
    await msg(1002, "换个说法", 21)
    check("内容不同且有插话可再记", n_msgs(1002) == 2, n_msgs(1002))

    # 无意义消息: 单字符 / 纯数字
    await msg(1006, "1", 22)
    await msg(1007, "好", 22)
    await msg(1008, "666", 22)
    check("纯数字不记录", n_msgs(1006) == 0 and n_msgs(1008) == 0)
    check("单字符不记录", n_msgs(1007) == 0)

    # 轮次间隔 ≥60 秒另起一轮
    await msg(1009, "早上好呀", 130)
    await msg(1009, "吃了吗", 190)  # 距上一条 60 秒 → 新轮次
    check("间隔满60秒另算一条", n_msgs(1009) == 2, n_msgs(1009))
    await msg(1009, "我走了", 210)  # 距上一条 20 秒且无插话 → 合并
    check("新轮次内连发再合并", n_msgs(1009) == 2, n_msgs(1009))

    # 用户例3: A一句 B三连发 A一句 → B只算1条
    await msg(1010, "wsjwio", 300)
    await msg(1011, "wjdid", 305)
    await msg(1011, "wksi", 310)
    await msg(1011, "qpsid", 315)
    await msg(1010, "sjdieowo", 320)
    check("例3: B连续三句只算1条", n_msgs(1011) == 1, n_msgs(1011))
    check("例3: A插话后两句都算", n_msgs(1010) == 2, n_msgs(1010))

    # 用户例1: A第1秒一句, B从第1秒一直说到约120秒 → 按60秒切分算2条
    await msg(1012, "先说一句", 1000)
    for sec in range(1001, 1120, 5):
        await msg(1013, f"消息{sec}", sec)
    check("例1: B连说120秒算2条", n_msgs(1013) == 2, n_msgs(1013))


    # 入群事件: 首次 +1条历史; 退群重进 → 2条历史 → 非新人
    raw = {"post_type": "notice", "notice_type": "group_increase",
           "group_id": GROUP, "user_id": 102, "operator_id": 201}
    await mh.handle_group_join(FakeEvent(102, "", raw=raw))
    h = conn1.execute(
        "SELECT COUNT(*) FROM join_history WHERE qq=102"
    ).fetchone()[0]
    check("首次入群记1条流水", h == 1, h)
    jb = conn1.execute("SELECT invited_by FROM join_log WHERE qq=102").fetchone()
    check("入群记录含邀请人", jb and jb[0] == 201, jb)

    c1 = conn1.cursor()
    check("首进者算新人", ms.is_newbie(102, "2026-09", c1))

    raw2 = dict(raw)
    await mh.handle_group_join(FakeEvent(102, "", raw=raw2))
    h = conn1.execute(
        "SELECT COUNT(*) FROM join_history WHERE qq=102"
    ).fetchone()[0]
    check("退了又进记第2条流水", h == 2, h)
    check("回流者永久失去新人资格", not ms.is_newbie(102, "2026-09", c1))

    # 老成员(机器人上线前已在群)重进: 无流水但members已有记录 → 补2条 → 非新人
    conn1.execute(
        "INSERT INTO members(qq, group_id, first_seen, last_sync) VALUES(900,?, 'x','x')",
        (GROUP,),
    )
    conn1.commit()
    raw3 = {"post_type": "notice", "notice_type": "group_increase",
            "group_id": GROUP, "user_id": 900, "operator_id": 0}
    await mh.handle_group_join(FakeEvent(900, "", raw=raw3))
    c1 = conn1.cursor()
    check(
        "上线前老成员重进不算新人",
        not ms.is_newbie(900, "2026-09", c1)
        and conn1.execute(
            "SELECT COUNT(*) FROM join_history WHERE qq=900"
        ).fetchone()[0] == 2,
    )


asyncio.run(db1())
conn1.close()

# ================= DB2: 窗口计分 / 扣分 / 邀请奖励 =================
print("== DB2: scoring window, penalty, invite bonus ==")
p2 = make_db("db2")
conn2 = sqlite3.connect(p2)

T = int(datetime.datetime(2099, 11, 20, 12, 0).timestamp())
for i in range(3):
    add_msg(conn2, 1001, "2099-11-20", T + i * 10)
for i in range(2):
    add_msg(conn2, 1002, "2099-11-20", T + 5 + i * 10)
add_msg(conn2, 1003, "2099-11-20", T + 3600)  # 孤立发言
for qq in (1001, 1002, 1003):
    conn2.execute(
        "INSERT INTO join_log(qq, group_id, invited_by, join_date) VALUES(?,?,0,'2099-11-01')",
        (qq, GROUP),
    )
conn2.execute(
    "INSERT INTO join_log(qq, group_id, invited_by, join_date) VALUES(1004,?,0,'2099-11-01')",
    (GROUP,),
)
conn2.commit()

vc = ds.valid_msg_counts(conn2, GROUP, "2099-11-20", 120, 2)
check("窗口内互有他人→A3条有效", vc.get(1001) == 3, vc)
check("窗口内互有他人→B2条有效", vc.get(1002) == 2, vc)
check("120秒内无人陪聊→C无效", 1003 not in vc, vc)

ds.calc_day("2099-11-20", DCFG, MCfg, GROUP, set())
r = conn2.execute(
    "SELECT score, sentences FROM daily_score WHERE qq=1001 AND date='2099-11-20'"
).fetchone()
check("A日分0.15/3句", r == (0.15, 3), r)
r2 = conn2.execute(
    "SELECT score FROM daily_score WHERE qq=1004 AND date='2099-11-20'"
).fetchone()
check("当月无分可扣→不产生负分", r2 is None, r2)

ds.calc_day("2099-11-21", DCFG, MCfg, GROUP, set())
r3 = conn2.execute(
    "SELECT score FROM daily_score WHERE qq=1001 AND date='2099-11-21'"
).fetchone()
check("未发言扣分扣完为止(-0.15)", r3 is not None and abs(r3[0] + 0.15) < 1e-9, r3)

# ---- 邀请奖励 ----
# 真新人101(邀请人201): 12-01..12-10 每天11条有效 → 达标A
# 回流新人103(邀请人202): 同样数据但2条流水 → 不达标(资格)
# 真新人105(邀请人203): 每天20条 → 也达标(条件A+B)
for qq, inv, jd in ((101, 201, "2099-12-01"), (103, 202, "2099-12-01"),
                    (105, 203, "2099-12-01")):
    conn2.execute(
        "INSERT INTO join_log(qq, group_id, invited_by, join_date) VALUES(?,?,?,?)",
        (qq, GROUP, inv, jd),
    )
    conn2.execute(
        "INSERT INTO join_history(qq, group_id, join_date, invited_by) VALUES(?,?,?,?)",
        (qq, GROUP, jd, inv),
    )
conn2.execute(
    "INSERT INTO join_history(qq, group_id, join_date, invited_by) VALUES(103,?, '2099-01-01', 0)",
    (GROUP,),
)
for day in range(1, 11):
    d = f"2099-12-{day:02d}"
    base = int(datetime.datetime(2099, 12, day, 12, 0).timestamp())
    for n_qq, cnt in ((101, 11), (103, 11), (105, 20)):
        for i in range(cnt):
            add_msg(conn2, n_qq, d, base + i * 10)
            add_msg(conn2, 999, d, base + i * 10 + 5)  # 陪聊
conn2.commit()

ds.calc_day("2099-12-10", DCFG, MCfg, GROUP, set())
b = dict(
    conn2.execute("SELECT qq, bonus FROM month_score WHERE month='2099-12'").fetchall()
)
check("真新人达标→邀请人+10", b.get(201) == 10, b)
check("回流新人不发邀请奖励", b.get(202) is None, b)
check("每天20条也达标", b.get(203) == 10, b)

ds.calc_day("2099-12-10", DCFG, MCfg, GROUP, set())
n = conn2.execute(
    "SELECT COUNT(*) FROM newbie_bonus WHERE newbie_qq=101"
).fetchone()[0]
check("邀请奖励不重复发", n == 1 and dict(
    conn2.execute("SELECT qq, bonus FROM month_score WHERE month='2099-12'").fetchall()
).get(201) == 10, n)

conn2.close()

# ================= DB3: 结算公式 / 并列平分 / 结转 / 幂等 =================
print("== DB3: settle formula, tie-split, carry-over, idempotent ==")
p3 = make_db("db3")
conn3 = sqlite3.connect(p3)

# u1 新人(当月入群): 日分和6.0 → ×1.1 = 6.6
# u2 连续获奖2月: 5.0 → ×0.8 = 4.0
# u3 有结转1.0: 3.0+1.0 = 4.0
# u4 直和: 4.0
#  → u2/u3/u4 并列4.0 = 边界第2名(top_n=2) → 平分 50/3=16.67; u1=50
# u5 日分和为负: max(0,-0.1)=0
# u6 普通 1.0 → 未获奖结转 0.05 进 2100-01
# u8 邀请分2.0 → 0+2.0
S = conn3.execute
S("INSERT INTO join_log VALUES(1001,?,0,'2099-12-05')", (GROUP,))
S("INSERT INTO join_history(qq, group_id, join_date) VALUES(1001,?,'2099-12-05')", (GROUP,))
for d, sc in (("2099-12-01", 2.0), ("2099-12-02", 2.0), ("2099-12-03", 2.0)):
    S("INSERT INTO daily_score VALUES(1001,?,?,20)", (d, sc))
for d, sc in (("2099-12-01", 3.0), ("2099-12-02", 2.0)):
    S("INSERT INTO daily_score VALUES(1002,?,?,20)", (d, sc))
for d, sc in (("2099-12-01", 2.0), ("2099-12-02", 1.0)):
    S("INSERT INTO daily_score VALUES(1003,?,?,20)", (d, sc))
S("INSERT INTO daily_score VALUES(1004,'2099-12-03',4.0,40)")
S("INSERT INTO daily_score VALUES(1005,'2099-12-03',-0.2,0)")
S("INSERT INTO daily_score VALUES(1005,'2099-12-04',0.1,2)")
S("INSERT INTO daily_score VALUES(1006,'2099-12-05',1.0,10)")
S("INSERT INTO award_history VALUES(1002,'2099-10')")
S("INSERT INTO award_history VALUES(1002,'2099-11')")
S("INSERT INTO month_score(qq, month, carry) VALUES(1003,'2099-12',1.0)")
S("INSERT INTO month_score(qq, month, bonus) VALUES(1008,'2099-12',2.0)")
conn3.commit()

MC = dict(MCfg)
MC["top_n"] = 2
MC["award_per_top"] = 50
summary = ms.settle("2099-12", MC)
rows = {r["qq"]: r for r in summary["rows"]}

check("新人系数 6.0×1.1=6.6", abs(rows[1001]["total"] - 6.6) < 1e-9, rows[1001])
check("衰减 5.0×0.8=4.0", abs(rows[1002]["total"] - 4.0) < 1e-9, rows[1002])
check("结转后置 3.0+1.0=4.0", abs(rows[1003]["total"] - 4.0) < 1e-9, rows[1003])
check("边界并列者各得 50/3", abs(rows[1004]["robux"] - 16.67) < 1e-9, rows[1004])
check("榜首拿满 50R", rows[1001]["robux"] == 50, rows[1001])
check("负分和不为负", abs(rows[1005]["total"] - 0.0) < 1e-9, rows[1005])
check("邀请分直接加", abs(rows[1008]["total"] - 2.0) < 1e-9, rows[1008])

nxt = dict(
    conn3.execute("SELECT qq, carry FROM month_score WHERE month='2100-01'").fetchall()
)
check("获奖者结转0", nxt.get(1001) == 0, nxt)
check("未获奖者结转 1.0×0.05", abs(nxt.get(1006, -1) - 0.05) < 1e-9, nxt)

summary2 = ms.settle("2099-12", MC)
rows2 = {r["qq"]: r for r in summary2["rows"]}
check(
    "重复结算幂等",
    all(abs(rows[q]["total"] - rows2[q]["total"]) < 1e-9 for q in rows),
    rows2,
)

robux_col = conn3.execute(
    "SELECT COUNT(*) FROM month_score WHERE month='2099-12' AND robux>0"
).fetchone()[0]
check("数据库月榜含robux列且已落库", robux_col == 4, robux_col)

import pathlib

from doors_bot.runtime_config import BASE_DIR as PLUGIN_DIR

# 发放名单与库同目录(库已迁出插件目录)，断言要按临时库所在目录找
payf = pathlib.Path(p3).parent / "pay_list_2099-12.txt"
check("发放名单文件含Robux列", payf.exists() and "Robux" in payf.read_text("utf-8"))
check("发放名单不落在插件目录", not (
    pathlib.Path(str(PLUGIN_DIR)) / "pay_list_2099-12.txt").exists())
if payf.exists():
    payf.unlink()

conn3.close()

# ================= DB4: 静默模式校验（QQ 无指令 / 指令仅限 WebUI） =================
print("== DB4: silent mode ==")
p4 = make_db("db4")
HERE = os.path.dirname(os.path.abspath(__file__))

main_src = pathlib.Path(HERE, "main.py").read_text("utf-8")
sched_src = pathlib.Path(HERE, "scheduler.py").read_text("utf-8")
report_src = pathlib.Path(HERE, "report.py").read_text("utf-8")
check("QQ 指令渲染模块已删除", not os.path.exists(os.path.join(HERE, "commands.py")))
n_cmd = main_src.count("@filter.command")
n_gate = main_src.count("PlatformAdapterType.WEBCHAT")
check("后台指令存在且全部仅限WebUI", n_cmd == 12 and n_cmd == n_gate, (n_cmd, n_gate))
n_alias = main_src.count('alias={"/')
check("后台指令带 /前缀 别名(修复ChatUI无响应)", n_alias == 12, n_alias)
check("main.py 无发送/回复代码", "send_message" not in main_src
      and "make_result" not in main_src and "reply" not in main_src)
check("scheduler.py 无主动发消息", "send_message" not in sched_src
      and "send_group_msg" not in sched_src and "send_private_msg" not in sched_src)
check("report.py 纯读库无发送", "send" not in report_src)
admin_src = pathlib.Path(HERE, "admin_ops.py").read_text("utf-8")
check("report.py 不写库", not any(
    k in report_src for k in ("INSERT ", "UPDATE ", "DELETE ")), )
check("改数据的操作集中在 admin_ops.py",
      "DELETE FROM" in admin_src and "INSERT" not in report_src)
check("admin_ops 不依赖 astrbot(本地CLI可用)", "astrbot" not in admin_src)
sched_src_text = sched_src
check("scheduler 不再引用已删除的 get_bot_qq",
      "get_bot_qq" not in sched_src and "get_no_score_qqs" in sched_src)
rc_text = pathlib.Path(HERE, "runtime_config.py").read_text("utf-8")
check("runtime_config 已移除管理员配置", "get_admin_qqs" not in rc_text)
check("runtime_config 已移除 get_bot_qq", "get_bot_qq" not in rc_text)
check("库目录在插件目录之外", "doors_bot_data" in rc_text
      and "from dotenv" not in rc_text)
schema_text = pathlib.Path(HERE, "_conf_schema.json").read_text("utf-8")
check("配置项 不计分QQ 取代了旧的 免扣分QQ/bot_qq",
      "bot_qq" not in schema_text and "no_score_qqs" in schema_text
      and "no_penalty_qqs" not in schema_text)
check("配置含补跑间隔与时区", "catchup_minutes" in schema_text
      and "timezone" in schema_text)
check("榜单条数默认为不限量", '"default": 0' in schema_text)

from doors_bot.main import DoorsBot  # noqa: E402


class FakePlatformMeta:
    id = "stub"


class FakePlatform:
    def meta(self):
        return FakePlatformMeta()


class FakePlatformMgr:
    platform_insts: list = []


class FakeContext:
    def __init__(self):
        self.platform_manager = FakePlatformMgr()
        self.tasks = []

    def register_task(self, task, desc):
        self.tasks.append(desc)
        task.close()


bot = DoorsBot(FakeContext(), {"db_path": p4, "group_id": str(GROUP)})
check("QQ 事件只读入口仍在",
      hasattr(bot, "group_message") and hasattr(bot, "group_notice"))
check("查看指令 rep_* 七个 + 运维指令 cmd_* 五个",
      sum(1 for n in dir(bot) if n.startswith("rep_")) == 7
      and sum(1 for n in dir(bot) if n.startswith("cmd_")) == 5,
      [n for n in dir(bot) if n.startswith(("rep_", "cmd_"))])
check("启动时既补跑也同步成员",
      "catchup_job" in main_src and "member_sync_job" in main_src)

# ================= DB5: 后台榜单渲染 =================
print("== DB5: report renderers ==")
from doors_bot import report  # noqa: E402

p5 = make_db("db5")
conn5 = sqlite3.connect(p5)
conn5.execute("INSERT INTO join_log VALUES(1001,?,0,'2099-12-05')", (GROUP,))
conn5.execute(
    "INSERT INTO join_history(qq, group_id, join_date) VALUES(1001,?,'2099-12-05')",
    (GROUP,),
)
conn5.execute("INSERT INTO daily_score VALUES(1001,'2099-12-03',5.0,50)")
conn5.execute("INSERT INTO daily_score VALUES(1002,'2099-12-03',2.0,20)")
conn5.execute("INSERT INTO month_score(qq, month, carry) VALUES(1002,'2099-12',1.0)")
# 并列名次 / 超长昵称 / 单日上限标注 的样本
for q, nm in ((1005, "昵称长到不行的用户甲乙丙"), (1006, "短名"),
              (1007, "这是一个非常非常非常长的群昵称一定会被截断到上限宽度")):
    conn5.execute("INSERT INTO user_info(qq, name) VALUES(?,?)", (q, nm))
for q, sent in ((1005, 60), (1006, 80), (1007, 45)):
    conn5.execute("INSERT INTO daily_score VALUES(?,'2099-12-03',4.0,?)", (q, sent))
conn5.commit()
ms.settle("2099-12", {**MCfg, "top_n": 1})

lm = report.render_last_month_board(now=datetime.datetime(2100, 1, 15, 12, 0))
check("上月榜含全月区间", "2099-12-01 ~ 2099-12-31" in lm, lm)
check("上月榜表头含名次与应发Robux列",
      lm.splitlines()[1].lstrip().startswith("名次") and "应发Robux" in lm, lm)
check("上月榜新人5.5分居首", lm.splitlines()[2].split()[0] == "1"
      and "5.5" in lm.splitlines()[2], lm)
check("上月榜按显示宽度对齐",
      len({report._width(l) for l in lm.splitlines()[1:]}) == 1, lm)

cm = report.render_current_month_board(now=datetime.datetime(2100, 1, 15, 12, 0))
check("本月榜含区间至当前", "2100-01-01" in cm and "01-15" in cm, cm)
check("本月榜含未获奖结转分", "0.15" in cm, cm)
check("本月榜奖金列", "50R" in cm and "奖金" in cm, cm)

db = report.render_day_board(
    "2099-11-20", now=datetime.datetime(2099, 11, 21, 10, 0)
)
check("日榜空数据提示", "暂无有效发言" in db, db)

# 同分并列 + 达上限标注 + 列宽不截断
d5 = report.render_day_board("2099-12-03", now=datetime.datetime(2099, 12, 5, 10, 0))
check("日榜达单日上限的得分带标注", d5.count("（已抵达今日上限）") == 4, d5)
check("日榜附单日上限说明", "单日上限 4 分" in d5 and "发言分上限 3" in d5, d5)
check("日榜同分并列名次(1,2,2,2,5)",
      [l.split()[0] for l in d5.splitlines()[2:] if not l.startswith("注:")]
      == ["1", "2", "2", "2", "5"], d5)
_tied2 = [l.split()[-1] for l in d5.splitlines()[2:]
          if not l.startswith("注:") and l.split()[0] == "2"]
check("日榜并列第2名内部按句数降序", _tied2 == ["80", "60", "45"], _tied2)
# 今日实时榜：两人都抵达单日上限(60/75 句)，同为第1名时按句数降序
T5 = int(datetime.datetime(2099, 12, 20, 12, 0).timestamp())
for i in range(75):
    add_msg(conn5, 1011, "2099-12-20", T5 + i * 2)
    if i < 60:
        add_msg(conn5, 1010, "2099-12-20", T5 + i * 2 + 1)
conn5.commit()
rt = report.render_day_board("2099-12-20", now=datetime.datetime(2099, 12, 20, 23, 0))
rt_rows = [l.split() for l in rt.splitlines()[2:] if not l.startswith("注:")]
check("实时榜两行同为第1名",
      [r[0] for r in rt_rows] == ["1", "1"]
      and all("已抵达今日上限" in r[3] for r in rt_rows), rt)
check("实时榜并列第一内部按句数降序", [r[-1] for r in rt_rows] == ["75", "60"], rt)
conn5.execute("DELETE FROM msg WHERE date='2099-12-20'")
conn5.commit()
check("日榜未截断32宽度以内的昵称", "昵称长到不行的用户甲乙丙" in d5, d5)
_rows5 = [l for l in d5.splitlines()[1:] if not l.startswith("注:")]
check("日榜各行等宽对齐", len({report._width(l) for l in _rows5}) == 1, _rows5)
check("月榜同分并列名次(1,2,2,2,5)",
      [l.split()[0] for l in lm.splitlines()[2:]] == ["1", "2", "2", "2", "5"], lm)
check("月榜超长昵称仍等宽",
      len({report._width(l) for l in lm.splitlines()[1:]}) == 1, lm)
check("左对齐列贴住列首(修 _pad 只认 left 导致整列右移)",
      report._pad("abc", 10, "l").startswith("abc")
      and report._pad("abc", 10, "left").startswith("abc"))


def _col_x(line, needle):
    return report._width(line[: line.index(needle)])


_h = lm.splitlines()[1]
_short = next(l for l in lm.splitlines()[2:] if "短名" in l)
_long = next(l for l in lm.splitlines()[2:] if "这是一个" in l)
check("昵称列起点与表头对齐", _col_x(_h, "用户") == _col_x(_short, "短名"),
      (_col_x(_h, "用户"), _col_x(_short, "短名")))
check("长短昵称起于同一列", _col_x(_short, "短名") == _col_x(_long, "这是一个"),
      (_col_x(_short, "短名"), _col_x(_long, "这是一个")))
check("超长昵称截断到上限宽度", report._width(_long.split()[1]) <= 32, _long)
check("榜单输出无emoji", not any(ch in lm + cm + db for ch in "📊🏆📈🥇🥈🥉※…→"),
      [lm, cm, db])
check("ChatUI 代码块围栏", report.render_today_board(
    now=datetime.datetime(2099, 11, 21, 10, 0), fenced=True).startswith("```"))

pay5 = pathlib.Path(str(PLUGIN_DIR)) / "pay_list_2099-12.txt"
if pay5.exists():
    pay5.unlink()
conn5.close()

# ================= DB6: 漏算补跑 / 月榜含未结算日 / 运维指令 =================
print("== DB6: catchup, live month merge, admin ops ==")
from doors_bot import admin_ops  # noqa: E402
from doors_bot import scheduler as sched  # noqa: E402

p6 = make_db("db6")
conn6 = sqlite3.connect(p6)
T6 = int(datetime.datetime(2099, 11, 20, 12, 0).timestamp())
for i in range(4):  # 2001/2002 互有对方在120秒窗内 → 各4句有效
    add_msg(conn6, 2001, "2099-11-20", T6 + i * 10)
    add_msg(conn6, 2002, "2099-11-20", T6 + 5 + i * 10)
for i in range(3):  # 2003 独占一天，没人陪聊 → 0 有效句
    add_msg(conn6, 2003, "2099-11-21", T6 + 86400 + i * 10)
conn6.execute(
    "INSERT INTO month_score(qq, month, carry) VALUES(2004,'2099-11',0)", ()
)
conn6.commit()

NOW6 = datetime.date(2099, 11, 25)
miss = sched.missing_calc_days("2099-11", "2099-11-25")
check("识别有发言却无日分的漏算日", miss == ["2099-11-20", "2099-11-21"], miss)

# 月榜在漏算状态下就该把当天的分算进去（用户报的"月榜没有昨天的数据"）
live6 = report.render_month_board("2099-11", now=datetime.datetime(2099, 11, 25, 10, 0))
check("月榜实时含漏算日的发言分", "0.2" in live6, live6)
check("月榜标注实时补算了哪几天", "未结算日" in live6 and "2099-11-20" in live6, live6)
check("0分不显示", "2004" not in live6, live6)

done = sched.run_catchup(miss)
check("补跑逐日重算", done == miss, done)
r6 = conn6.execute(
    "SELECT score, sentences FROM daily_score WHERE qq=2001 AND date='2099-11-20'"
).fetchone()
check("补跑分数与句数正确(4句0.2)", r6 and abs(r6[0] - 0.2) < 1e-9 and r6[1] == 4, r6)
check("整天无有效发言不凭空造行",
      conn6.execute(
          "SELECT COUNT(*) FROM daily_score WHERE qq=2003"
      ).fetchone()[0] == 0)
check("尝试过的日期记入台账防重复补跑", "2099-11-21" in sched._CATCH_SEEN)
after = report.render_month_board("2099-11", now=datetime.datetime(2099, 11, 25, 10, 0))
check("补跑后分数不变且漏算提示消失",
      "0.2" in after and "2099-11-20" not in after.split("含")[0], after)

# 榜单条数：1 → 截断；0 → 完整显示
cfg_all = {
    "db_path": p6, "group_id": str(GROUP),
    "report": {"display_limit": 1},
}
set_plugin_config(cfg_all)
one = report.render_month_board("2099-11", now=datetime.datetime(2099, 11, 25, 10, 0))
check("display_limit=1 只留一行并提示省略",
      one.count("2099-11-20") == 0 and "另有 1 行未显示" in one, one)
set_plugin_config({**cfg_all, "report": {"display_limit": 0}})
full = report.render_month_board("2099-11", now=datetime.datetime(2099, 11, 25, 10, 0))
check("display_limit=0 完整显示不省略",
      "另有" not in full and "2001" in full and "2002" in full, full)

# ---- 参数解析 ----
check("日期支持中文与多种格式",
      admin_ops.parse_date("昨天", NOW6) == "2099-11-24"
      and admin_ops.parse_date("前天", NOW6) == "2099-11-23"
      and admin_ops.parse_date("24", NOW6) == "2099-11-24"
      and admin_ops.parse_date("2099-11-20", NOW6) == "2099-11-20"
      and admin_ops.parse_date("", NOW6) == "2099-11-25")
check("非法日期返回None", admin_ops.parse_date("不是日期", NOW6) is None)
check("月份支持本月/上月/裸月",
      admin_ops.parse_month("", NOW6) == "2099-11"
      and admin_ops.parse_month("上月", NOW6) == "2099-10"
      and admin_ops.parse_month("11", NOW6) == "2099-11"
      and admin_ops.parse_month("2099-11", NOW6) == "2099-11")

# ---- 重置日：按 msg 重算，未过完的当天不扣分 ----
add_msg(conn6, 2001, "2099-11-20", T6 + 40)
conn6.commit()
txt = admin_ops.reset_day("2099-11-20", now=NOW6)
r6b = conn6.execute(
    "SELECT score, sentences FROM daily_score WHERE qq=2001 AND date='2099-11-20'"
).fetchone()
check("重置日按发言记录重算(5句0.25)",
      "重置日 2099-11-20 完成" in txt and abs(r6b[0] - 0.25) < 1e-9
      and r6b[1] == 5, (txt, r6b))
today_txt = admin_ops.reset_day("2099-11-25", now=NOW6)
check("重置未过完的当天跳过未发言扣分", "当天尚未过完" in today_txt, today_txt)
check("未来日期拒绝重置", "尚未到来" in admin_ops.reset_day("2099-12-01", now=NOW6))

# ---- 删榜项：删掉某人本月记录，重新开始 ----
del_txt = admin_ops.delete_board_row(2002, "2099-11", now=NOW6)
left = conn6.execute(
    "SELECT COUNT(*) FROM daily_score WHERE qq=2002 AND date LIKE '2099-11%'"
).fetchone()[0]
check("删榜项清空该人本月日分并回显被删行",
      left == 0 and "被删日分" in del_txt and "2002" in del_txt, (del_txt, left))
check("删不存在的人不误删", "没有任何记录" in admin_ops.delete_board_row(777, "2099-11", now=NOW6))

# ---- 结算：已过完的月直接结，未过完的要确认词 ----
st = admin_ops.settle_month("2099-11", now=datetime.date(2099, 12, 1))
check("结算已完成月份", "结算 2099-11 完成" in st, st)
settled = conn6.execute(
    "SELECT COUNT(*) FROM month_score WHERE month='2099-11' AND settled=1"
).fetchone()[0]
check("结算落库 settled=1", settled >= 1, settled)
check("已结算月份拒绝删榜项",
      "已结算" in admin_ops.delete_board_row(2001, "2099-11", now=datetime.date(2099, 12, 1)))
check("未过完的月份需要确认词",
      "确认" in admin_ops.settle_month("2099-12", now=datetime.date(2099, 12, 25)))
rm = admin_ops.reset_month("2099-11", now=datetime.date(2099, 12, 1))
check("重置已结算月会提示再结算", "已结算" in rm and "结算" in rm, rm)

# ---- 补跑指令文案 ----
check("无漏算时补跑报告正常", "没有漏算" in admin_ops.manual_catchup(datetime.date(2099, 11, 25)))

conn6.close()

# ================= DB7: 群成员同步自愈 + 新人加成/扣分名单 =================
print("== DB7: member sync self-heal ==")
p7 = make_db("db7")
conn7 = sqlite3.connect(p7)
JOIN_TS = int(datetime.datetime(2099, 11, 10, 8, 0).timestamp())


class FakeMeta:
    def __init__(self, pid="fake"):
        self.id = pid


class FakeBot:
    def __init__(self, members=None, err=None):
        self._members = members
        self._err = err
        self.calls = []

    async def call_action(self, action, **kw):
        self.calls.append((action, kw))
        if self._err:
            raise RuntimeError(self._err)
        return self._members


class FakePlat:
    def __init__(self, bot, pid="fake"):
        self.bot = bot
        self._meta = FakeMeta(pid)

    def meta(self):
        return self._meta


class Ctx:
    def __init__(self, plats):
        self.platform_manager = types.SimpleNamespace(platform_insts=plats)


MEMBERS = [
    {"user_id": 3001, "card": "新人甲", "nickname": "n3001", "role": "member",
     "join_time": JOIN_TS},
    {"user_id": 3002, "card": "", "nickname": "n3002", "role": "admin",
     "join_time": 0},
]

sched.set_context(Ctx([FakePlat(FakeBot(err="failed to fetch"))]))
asyncio.run(sched.member_sync_job())
check("同步失败不落库且记录错误原因",
      conn7.execute("SELECT COUNT(*) FROM members").fetchone()[0] == 0
      and "failed to fetch" in sched.STATE["sync"]["error"],
      sched.STATE["sync"])

sched.set_context(Ctx([FakePlat(FakeBot()), FakePlat(FakeBot(MEMBERS), "napcat2")]))
SYNC1 = datetime.datetime(2099, 11, 10, 9, 0, 0)
asyncio.run(sched.member_sync_job(SYNC1))
st = sched.STATE["sync"]
check("首个平台失败自动试下一个", st["count"] == 2 and st["platform"] == "napcat2", st)
m = conn7.execute(
    "SELECT qq, join_date, nickname, in_group FROM members ORDER BY qq"
).fetchall()
check("成员含入群日期与昵称", m == [(3001, "2099-11-10", "新人甲", 1),
                                   (3002, None, "n3002", 1)], m)
jl = dict(conn7.execute("SELECT qq, join_date FROM join_log").fetchall())
check("插件安装前入群者回填join_log(修新人无加成)",
      jl.get(3001) == "2099-11-10" and 3002 not in jl, jl)
check("回填后当年当月入群算新人",
      ms.is_newbie(3001, "2099-11", conn7.cursor()))
check("回填后进未发言扣分名单",
      ds.roster_with_join_dates(conn7, GROUP).get(3001) == "2099-11-10",
      ds.roster_with_join_dates(conn7, GROUP))
check("昵称同步到user_info",
      conn7.execute("SELECT name FROM user_info WHERE qq=3001").fetchone()[0] == "新人甲")

# 第二次同步：3002 退群 → in_group=0 → 退出扣分名单，但保留历史
sched.set_context(Ctx([FakePlat(FakeBot(MEMBERS[:1]))]))
asyncio.run(sched.member_sync_job(SYNC1 + datetime.timedelta(hours=1)))
check("退群成员标记in_group=0",
      conn7.execute(
          "SELECT in_group FROM members WHERE qq=3002"
      ).fetchone()[0] == 0)
check("退群成员不再进扣分名单",
      3002 not in ds.roster_with_join_dates(conn7, GROUP))

sched.set_context(Ctx([]))
asyncio.run(sched.member_sync_job())
check("无可用平台时安全跳过", "没有可用" in sched.STATE["sync"]["error"]
      or "call_action" in sched.STATE["sync"]["error"], sched.STATE["sync"])
conn7.close()



# ================= DB8: 默认数据目录解析（v2.4.1：插件同级） =================
print("== DB8: default data dir ==")
from doors_bot import runtime_config as rc  # noqa: E402

_base = rc.BASE_DIR
try:
    fake_root = pathlib.Path(TMP) / "AstrBot"
    fake_plugin = fake_root / "data" / "plugins" / "doors_bot"
    rc.BASE_DIR = fake_plugin
    check("部署形态识别 AstrBot 根", rc._deployed_root() == fake_root,
          rc._deployed_root())
    check("默认数据目录在 data/plugins 下与插件同级",
          rc._default_data_dir() == fake_root / "data" / "plugins" / "doors_bot_data",
          rc._default_data_dir())
    check("默认数据目录不在插件目录内",
          rc._default_data_dir().parent == fake_plugin.parent
          and rc._default_data_dir() != fake_plugin)
    check("v2.4.0 旧默认位置纳入遗留探测",
          rc._prev_default_db() == fake_root.parent / "doors_bot_data" / "doors.db",
          rc._prev_default_db())

    rc.BASE_DIR = pathlib.Path(TMP) / "devcopy" / "doors_bot"
    check("开发副本(不在 plugins 下)识别不到 AstrBot 根",
          rc._deployed_root() is None)
    check("开发副本默认落在插件同级 doors_bot_data",
          rc._default_data_dir() == pathlib.Path(TMP) / "devcopy" / "doors_bot_data",
          rc._default_data_dir())
    rc.BASE_DIR = fake_plugin

    set_plugin_config({"db_path": "", "group_id": str(GROUP)})
    check("db_path 留空即用默认数据目录",
          os.path.abspath(rc.get_db_path()) == os.path.abspath(
              str(fake_root / "data" / "plugins" / "doors_bot_data" / "doors.db")),
          rc.get_db_path())
    check("README 写明新的默认数据目录",
          all(s in pathlib.Path(HERE, "README.md").read_text("utf-8")
              for s in ("data\\plugins\\doors_bot_data\\doors.db", "不要写")),
          )
    check("配置页提示与代码默认一致",
          "data/plugins/doors_bot_data/doors.db" in schema_text, schema_text[:120])
finally:
    rc.BASE_DIR = _base
    set_plugin_config({"db_path": p4, "group_id": str(GROUP)})

# ================= DB9: 不计分QQ（群内机器人完全不参与） =================
print("== DB9: no-score qqs ==")
BOT = 8880001
p9 = make_db("db9")
conn9 = sqlite3.connect(p9)
set_plugin_config({
    "db_path": p9, "group_id": str(GROUP), "no_score_qqs": f"{BOT}, 8880002",
})
T9 = int(datetime.datetime(2099, 11, 20, 12, 0).timestamp())
for qq in (BOT, 4001, 4002, 4003):
    conn9.execute(
        "INSERT INTO members(qq, group_id, nickname, join_date, in_group) "
        "VALUES(?,?,'n','2099-11-01',1)",
        (qq, GROUP),
    )
# 配置之前机器人已经入库的历史发言：也不该替别人凑"有人接话"
add_msg(conn9, BOT, "2099-11-20", T9)
add_msg(conn9, BOT, "2099-11-20", T9 + 10)
add_msg(conn9, 4001, "2099-11-20", T9 + 2)
add_msg(conn9, 4002, "2099-11-20", T9 + 300)
add_msg(conn9, 4003, "2099-11-20", T9 + 305)
conn9.commit()

vc9 = ds.valid_msg_counts(conn9, GROUP, "2099-11-20", 120, 2)
check("不计分QQ自己的发言不计数", BOT not in vc9, vc9)
check("只有机器人接话不算有效发言", 4001 not in vc9, vc9)
check("正常两人互相接话仍计分", vc9.get(4002) == 1 and vc9.get(4003) == 1, vc9)


async def msg9(qq, text, off):
    await mh.handle_group_message(FakeEvent(qq, text), _now=T9 + off)


asyncio.run(msg9(BOT, "机器人自述", 20))
asyncio.run(msg9(4002, "人类发言", 400))
check("不计分QQ的新发言根本不入库",
      conn9.execute("SELECT COUNT(*) FROM msg WHERE qq=?", (BOT,)).fetchone()[0] == 2)
check("普通成员发言照常入库",
      conn9.execute("SELECT COUNT(*) FROM msg WHERE qq=4002").fetchone()[0] == 2)

# 两人都攒了可扣的分：机器人不该被扣分，人该被扣
for qq in (BOT, 4001):
    conn9.execute("INSERT INTO daily_score VALUES(?, '2099-11-10', 0.5, 10)", (qq,))
conn9.commit()
ds.calc_day("2099-11-20", DCFG, MCfg, GROUP)
rows9 = dict(conn9.execute(
    "SELECT qq, score FROM daily_score WHERE date='2099-11-20'").fetchall())
check("不计分QQ不进日分表(既不加分也不扣分)", BOT not in rows9, rows9)
check("有接话的人加分", abs(rows9.get(4002, 0) - 0.05) < 1e-9, rows9)
check("只有机器人接话的人按未发言扣分",
      4001 in rows9 and abs(rows9[4001] + 0.2) < 1e-9, rows9)
diag9 = report.render_diag()
check("自检输出列出不计分QQ", "不计分" in diag9 and str(BOT) in diag9, diag9)
set_plugin_config({"db_path": p9, "group_id": str(GROUP)})
check("名单留空即不过滤(机器人也参与计分)",
      BOT in ds.valid_msg_counts(conn9, GROUP, "2099-11-20", 120, 2)
      and 4001 in ds.valid_msg_counts(conn9, GROUP, "2099-11-20", 120, 2))
conn9.close()

# 汇总
print(f"\n结果: {PASS} 通过, {FAIL} 失败")
import shutil

shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)
