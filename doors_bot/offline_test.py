"""
doors_bot 离线测试（不需要真实 AstrBot，stub 掉 astrbot 模块）

运行: 在 doors_bot 的父目录执行  python doors_bot/offline_test.py
      或                        python offline_test.py (自动加父目录到 sys.path)
"""

from __future__ import annotations

import asyncio
import datetime
import os
import re
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

_CFG_BASE = {
    "group_id": str(GROUP),
    "invite_bonus": {"enabled": True, "window_days": 10,
                     "daily_min_msgs": 10, "avg_daily_msgs": 20, "bonus": 10},
}


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
    set_plugin_config({**_CFG_BASE, "db_path": path})
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

    # 入群那一刻是真实时间，所以"当月新人"只能按运行当天的月份判，写死月份会在跨天时报错
    this_month = datetime.date.today().strftime("%Y-%m")
    c1 = conn1.cursor()
    check("首进者算新人", ms.is_newbie(102, this_month, c1))

    raw2 = dict(raw)
    await mh.handle_group_join(FakeEvent(102, "", raw=raw2))
    h = conn1.execute(
        "SELECT COUNT(*) FROM join_history WHERE qq=102"
    ).fetchone()[0]
    check("退了又进记第2条流水", h == 2, h)
    check("回流者永久失去新人资格", not ms.is_newbie(102, this_month, c1))

    # 主动入群(群号/二维码): operator_id 就是本人，不能算自己邀请自己
    raw_self = {"post_type": "notice", "notice_type": "group_increase",
                "group_id": GROUP, "user_id": 905, "operator_id": 905}
    await mh.handle_group_join(FakeEvent(905, "", raw=raw_self))
    check("自己主动入群时邀请人记为0", conn1.execute(
        "SELECT invited_by FROM join_log WHERE qq=905").fetchone()[0] == 0)

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
        not ms.is_newbie(900, this_month, c1)
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
# 新人107(204 只是放行他的管理员)/109(205 是绑过的新人邀请人): 数据与101相同
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
for qq, inv, src in ((107, 204, 1), (109, 205, 2)):
    conn2.execute(
        "INSERT INTO join_log(qq, group_id, invited_by, join_date, invited_src, "
        "join_kind) VALUES(?,?,?,'2099-12-01',?,'approve')", (qq, GROUP, inv, src),
    )
    conn2.execute(
        "INSERT INTO join_history(qq, group_id, join_date, invited_by) "
        "VALUES(?,?,'2099-12-01',?)", (qq, GROUP, inv),
    )
conn2.execute(
    "INSERT INTO join_history(qq, group_id, join_date, invited_by) VALUES(103,?, '2099-01-01', 0)",
    (GROUP,),
)
for day in range(1, 11):
    d = f"2099-12-{day:02d}"
    base = int(datetime.datetime(2099, 12, day, 12, 0).timestamp())
    for n_qq, cnt in ((101, 11), (103, 11), (105, 20), (107, 11), (109, 11)):
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
check("管理员审批放行的入群不给审批人拉新分", b.get(204) is None, b)
check("审批入群但邀请人自己绑定确认过的照常给分", b.get(205) == 10, b)

ds.calc_day("2099-12-10", DCFG, MCfg, GROUP, set())
n = conn2.execute(
    "SELECT COUNT(*) FROM newbie_bonus WHERE newbie_qq=101"
).fetchone()[0]
check("邀请奖励不重复发", n == 1 and dict(
    conn2.execute("SELECT qq, bonus FROM month_score WHERE month='2099-12'").fetchall()
).get(201) == 10, n)


# ---- 拉新分记到哪个月：满期那天在下一月，账仍要记回入群那月 ----
def _days_from(start: datetime.date, count: int):
    return [start + datetime.timedelta(days=i) for i in range(count)]


def _seed_newbie(qq, inv, join_day):
    conn2.execute(
        "INSERT INTO join_log(qq, group_id, invited_by, join_date) VALUES(?,?,?,?)",
        (qq, GROUP, inv, join_day.strftime("%Y-%m-%d")),
    )
    conn2.execute(
        "INSERT INTO join_history(qq, group_id, join_date, invited_by) VALUES(?,?,?,?)",
        (qq, GROUP, join_day, inv),
    )
    for d in _days_from(join_day, 10):
        base = int(datetime.datetime(d.year, d.month, d.day, 12, 0).timestamp())
        for i in range(11):
            add_msg(conn2, qq, d.strftime("%Y-%m-%d"), base + i * 10)
            add_msg(conn2, 999, d.strftime("%Y-%m-%d"), base + i * 10 + 5)


def _awarded_month(qq):
    row = conn2.execute(
        "SELECT awarded_month FROM newbie_bonus WHERE newbie_qq=?", (qq,)
    ).fetchone()
    return row[0] if row else None


_seed_newbie(111, 211, datetime.date(2099, 12, 25))
conn2.commit()
ds.calc_day("2100-01-02", DCFG, MCfg, GROUP, set())
check("观察期没满不发分（12-25 入群满期在 1-03）",
      _awarded_month(111) is None, _awarded_month(111))
ds.calc_day("2100-01-03", DCFG, MCfg, GROUP, set())
check("满期当晚就发分，账记在新人入群的那个月而不是满期月",
      _awarded_month(111) == "2099-12"
      and dict(conn2.execute(
          "SELECT qq, bonus FROM month_score WHERE month='2099-12'").fetchall()
      ).get(211) == 10
      and conn2.execute(
          "SELECT 1 FROM month_score WHERE qq=211 AND month='2100-01'"
      ).fetchone() is None,
      (_awarded_month(111),
       conn2.execute("SELECT qq, month, bonus FROM month_score WHERE qq=211")
       .fetchall()))

# 入群月已经封账：再往里加分只改榜单、换不来钱，所以要退到当前月
conn2.execute("UPDATE month_score SET settled=1 WHERE month='2099-12'")
conn2.commit()
_seed_newbie(112, 212, datetime.date(2099, 12, 26))
conn2.commit()
ds.calc_day("2100-01-04", DCFG, MCfg, GROUP, set())
check("入群月已结算时拉新分退记到当前月",
      _awarded_month(112) == "2100-01"
      and dict(conn2.execute(
          "SELECT qq, bonus FROM month_score WHERE month='2100-01'").fetchall()
      ).get(212) == 10,
      (_awarded_month(112),
       conn2.execute("SELECT qq, month, bonus, settled FROM month_score WHERE qq=212")
       .fetchall()))

conn2.close()

# ================= DB3: 结算公式 / 奖金池封顶 / 结转 / 幂等 =================
print("== DB3: settle formula, tie-split, carry-over, idempotent ==")
p3 = make_db("db3")
conn3 = sqlite3.connect(p3)

# u1 新人(当月入群): 日分和6.0 → ×1.1 = 6.6
# u2 连续获奖2月: 5.0 → ×0.8 = 4.0
# u3 有结转1.0: 3.0+1.0 = 4.0
# u4 直和: 4.0
#  → u2/u3/u4 并列4.0 = 边界第2名(top_n=2) → 榜首占掉 1 个名额，这 3 人平分剩下
#    1×50=50R，每人 16.66R（总额 99.98R ≤ 奖金池 100R）; u1=50
# u5 日分和为负: max(0,-0.1)=0
# u6 普通 1.0 → 未获奖结转 0.05 进 2100-01
# u8 邀请分2.0 → 0+2.0
S = conn3.execute
S("INSERT INTO join_log(qq, group_id, invited_by, join_date) VALUES(1001,?,0,'2099-12-05')", (GROUP,))
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
MC["decay_rate"] = 0.1  # 这一节验证公式，衰减率显式钉住(代码默认为 0.5)
check("连续获奖衰减率默认 0.5", MCfg["decay_rate"] == 0.5, MCfg)
summary = ms.settle("2099-12", MC)
rows = {r["qq"]: r for r in summary["rows"]}

check("新人系数 6.0×1.1=6.6", abs(rows[1001]["total"] - 6.6) < 1e-9, rows[1001])
check("衰减 5.0×0.8=4.0", abs(rows[1002]["total"] - 4.0) < 1e-9, rows[1002])
check("结转后置 3.0+1.0=4.0", abs(rows[1003]["total"] - 4.0) < 1e-9, rows[1003])
check("边界并列组平分剩下的 1 个名额(50÷3 向下取 2 位=16.66R)",
      abs(rows[1004]["robux"] - 16.66) < 1e-9, rows[1004])
check("并列超出名额时总额封顶在奖金池 2×50=100R 内",
      abs(sum(r["robux"] for r in rows.values()) - 99.98) < 1e-9,
      {q: r["robux"] for q, r in rows.items()})
check("榜首拿满 50R", rows[1001]["robux"] == 50, rows[1001])

# 奖金池口径：池子 = top_n × 单份，并列整组平分该组占掉的剩余名额，永不超发
check("并列跨边界但装得下名额时每人拿满",
      ms.compute_awards([(1, 100), (2, 90), (3, 90), (4, 80)], 3, 50)
      == {1: 50.0, 2: 50.0, 3: 50.0})
check("并列装不下时整组平分剩余名额(第2名3人并列抢1个名额→33.33R)",
      ms.compute_awards([(1, 10), (2, 9), (3, 9), (4, 9)], 3, 50)
      == {1: 50.0, 2: 33.33, 3: 33.33, 4: 33.33})
check("全员并列超出名额时整组平分",
      ms.compute_awards([(1, 5), (2, 5), (3, 5)], 1, 50)
      == {1: 16.66, 2: 16.66, 3: 16.66})
check("人数不足 top_n 时全员获奖",
      ms.compute_awards([(1, 3), (2, 2)], 10, 50) == {1: 50.0, 2: 50.0})
check("低于第N名分数线一分不得",
      ms.compute_awards([(1, 10), (2, 9), (3, 8), (4, 1)], 3, 50)
      == {1: 50.0, 2: 50.0, 3: 50.0})
check("空榜或 top_n=0 不发奖",
      ms.compute_awards([], 10, 50) == {} and ms.compute_awards([(1, 5)], 0, 50) == {})

# 用户给的例子：前 6 人无并列，第 7 名起 7 人并列(顺位排到第 13)，池子 10×50=500R
_r7 = [(q, 100.0 - q) for q in range(1, 7)] + [(q, 90.0) for q in range(7, 14)] + [(14, 80.0)]
_a7 = ms.compute_awards(_r7, 10, 50)
check("第7名起7人并列：13 人获奖，前 6 人各 50R",
      len(_a7) == 13 and all(_a7[q] == 50.0 for q in range(1, 7)), _a7)
check("并列的第7~13名平分第7~10名那 (10-7+1)×50=200R",
      all(abs(_a7[q] - 28.57) < 1e-9 for q in range(7, 14)), {q: _a7[q] for q in range(7, 14)})
check("平分后总额 499.99R 不超过奖金池 500R",
      abs(sum(_a7.values()) - 499.99) < 1e-9 and sum(_a7.values()) <= 500, sum(_a7.values()))
check("池子见底后更低分者无奖金", 14 not in _a7, _a7)
check("任意并列形态总额都不超奖金池(穷举 1~12 人同分组)",
      all(sum(ms.compute_awards(
              [(q, 100.0 - q) for q in range(1, 6)]
              + [(q, 90.0) for q in range(6, 6 + g)], 10, 50).values()) <= 500.0
          for g in range(1, 13)))
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
mh_src = pathlib.Path(HERE, "message_handler.py").read_text("utf-8")
check("QQ 指令渲染模块已删除", not os.path.exists(os.path.join(HERE, "commands.py")))
n_cmd = main_src.count("@filter.command")
n_gate = main_src.count("PlatformAdapterType.WEBCHAT")
check("后台指令全部仅限WebUI，只有 新人绑定 例外(邀请人不会去开后台)",
      n_cmd == 14 and n_gate == 13
      and "platform_adapter_type" not in
      main_src.split("async def cmd_bind_inviter")[0].rsplit("@filter.command", 1)[-1],
      (n_cmd, n_gate))
n_alias = main_src.count('alias={"/')
check("后台指令带 /前缀 别名(修复ChatUI无响应)", n_alias == 14, n_alias)
_no_desc = [
    m.group(1) for m in re.finditer(r'@filter\.command\((.*?)\)\n', main_src, re.S)
    if "desc=" not in m.group(1)
]
check("每条指令都写了 desc(AstrBot 后台的指令简介取 desc，没有就显示无描述)",
      len(_no_desc) == 0 and main_src.count("desc=") >= 14, _no_desc)
check("main.py 无发送/回复代码", "send_message" not in main_src
      and "make_result" not in main_src and "reply" not in main_src)
_gn_seg = main_src.split("async def group_notice")[0].rsplit("@filter", 1)[-1]
check("入群通知处理器放开到 ALL(AstrBot 把带群号的通知转成 GROUP_MESSAGE)",
      "event_message_type(filter.EventMessageType.ALL)" in _gn_seg, _gn_seg)
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
check("查看指令 rep_* 六个 + 运维与群内指令 cmd_* 八个",
      sum(1 for n in dir(bot) if n.startswith("rep_")) == 6
      and sum(1 for n in dir(bot) if n.startswith("cmd_")) == 8
      and hasattr(bot, "cmd_bind_inviter"),
      [n for n in dir(bot) if n.startswith(("rep_", "cmd_"))])
check("/查询 是只读的 rep_ 指令，不套二次确认",
      "async def rep_profile" in main_src
      and "_guarded" not in main_src.split("async def rep_profile")[1].split("def cmd_")[0],
      main_src.split("async def rep_profile")[1][:200])
check("改名后的指令已登记，旧名不再存在",
      all(x in main_src for x in ('"日榜"', '"月榜"', '"删除"', '"扣除"'))
      and not any(x in main_src for x in ('"今日榜"', '"本月榜"', '"删榜项"')),
      [x for x in ('今日榜', '本月榜', '删榜项') if f'"{x}"' in main_src])
check("除查看外每条运维指令都要确认",
      main_src.count("self._guarded(") == 6
      and main_src.count("admin_ops.split_confirm(") == 10,
      # 7 条运维(含 /同步) + 3 条查看(日榜/月榜/查询 容忍末尾多打一个"确认")
      (main_src.count("self._guarded("), main_src.count("admin_ops.split_confirm(")))
check("启动时既补跑也同步成员",
      "catchup_job" in main_src and "member_sync_job" in main_src)

# ================= DB5: 后台榜单渲染 =================
print("== DB5: report renderers ==")
from doors_bot import report  # noqa: E402

p5 = make_db("db5")
conn5 = sqlite3.connect(p5)


def _fixed_w(line: str, widths: tuple[int, ...]) -> int:
    """定宽那几列的显示宽度；末尾自由列（备注）多长都不算进来。"""
    target = sum(widths) + 2 * (len(widths) - 1)
    used, i = 0, 0
    while i < len(line) and used < target:
        used += report._width(line[i])
        i += 1
    return used if used >= target else -1


conn5.execute(
    "INSERT INTO join_log(qq, group_id, invited_by, join_date) VALUES(1001,?,0,'2099-12-05')",
    (GROUP,),
)
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
      len({_fixed_w(l, report.MONTH_WIDTHS) for l in lm.splitlines()[1:]}) == 1, lm)

cm = report.render_current_month_board(now=datetime.datetime(2100, 1, 15, 12, 0))
check("本月榜含区间至当前", "2100-01-01" in cm and "01-15" in cm, cm)
check("本月榜含未获奖结转分", "0.15" in cm, cm)
check("本月榜奖金列", "50R" in cm and "奖金" in cm, cm)

db = report.render_day_board(
    "2099-11-20", now=datetime.datetime(2099, 11, 21, 10, 0)
)
check("日榜空数据提示", "暂无有效发言" in db, db)


def _split_fixed(line, widths):
    """按已知列宽把定宽表的一行切回各列，返回 (定宽列, 末尾自由列)。"""
    cells, pos = [], 0
    for w in widths:
        seg, used = [], 0
        while pos < len(line) and used < w:
            used += report._width(line[pos])
            seg.append(line[pos])
            pos += 1
        cells.append("".join(seg))
        pos += 2
    return cells, line[pos:]


# 同分并列 + 达上限标注 + 列宽不截断
d5 = report.render_day_board("2099-12-03", now=datetime.datetime(2099, 12, 5, 10, 0))
_cut5 = [l for l in d5.splitlines()[2:] if not l.startswith("注:")]
check("日榜达上限的标注写在得分列",
      sum(1 for l in _cut5
          if _split_fixed(l, report.DAY_WIDTHS)[0][3].endswith(report.CAP_TAG)) == 4, d5)
check("日榜得分列不再靠备注解释达上限",
      all(report._day_note(4.0, get_daily_config()) not in l for l in _cut5), d5)
check("日榜附单日上限说明", "单日上限 4 分" in d5 and "发言分上限 3" in d5, d5)
check("达上限标注的写法就是 已达今日上限",
      report.CAP_TAG == "已达今日上限" and report.CAP_TAG in d5, d5)
check("上限换成两位数时得分列跟着加宽，标注不被截断",
      report._day_widths({"daily_cap": 98, "bonus_score": 2})[3]
      == report._width("100 " + report.CAP_TAG)
      and report._width(report._score_cell(100, {"daily_cap": 98, "bonus_score": 2}))
      <= report._day_widths({"daily_cap": 98, "bonus_score": 2})[3],
      report._score_cell(100, {"daily_cap": 98, "bonus_score": 2}))
check("日榜同分并列名次(1,2,2,2,5)",
      [l.split()[0] for l in d5.splitlines()[2:] if not l.startswith("注:")]
      == ["1", "2", "2", "2", "5"], d5)
_tied2 = [_split_fixed(l, report.DAY_WIDTHS)[0][4].strip() for l in _cut5
          if _split_fixed(l, report.DAY_WIDTHS)[0][0].strip() == "2"]
check("日榜并列第2名内部按句数降序", _tied2 == ["80", "60", "45"], _tied2)
# 今日实时榜：两人都抵达单日上限(60/75 句)，同为第1名时按句数降序
T5 = int(datetime.datetime(2099, 12, 20, 12, 0).timestamp())
for i in range(75):
    add_msg(conn5, 1011, "2099-12-20", T5 + i * 2)
    if i < 60:
        add_msg(conn5, 1010, "2099-12-20", T5 + i * 2 + 1)
conn5.commit()
rt = report.render_day_board("2099-12-20", now=datetime.datetime(2099, 12, 20, 23, 0))
_rt = [_split_fixed(l, report.DAY_WIDTHS)[0]
       for l in rt.splitlines()[2:] if not l.startswith("注:")]
check("实时榜两行同为第1名",
      [r[0].strip() for r in _rt] == ["1", "1"]
      and all(report.CAP_TAG in r[3] for r in _rt), rt)
check("实时榜并列第一内部按句数降序", [r[4].strip() for r in _rt] == ["75", "60"], rt)
conn5.execute("DELETE FROM msg WHERE date='2099-12-20'")
conn5.commit()
# 并列把奖金池撑爆时，实时月榜要显示"整组平分、封顶不超发"的说明
conn5.execute("UPDATE month_score SET settled=0 WHERE month='2099-12'")
conn5.commit()
set_plugin_config({**_CFG_BASE, "db_path": p5, "monthly_settle": {**MCfg, "top_n": 3}})
_splitm = report.render_month_board("2099-12", now=datetime.datetime(2100, 1, 15, 12, 0))
set_plugin_config({**_CFG_BASE, "db_path": p5})
conn5.execute("UPDATE month_score SET settled=1 WHERE month='2099-12'")
conn5.commit()
check("实时月榜：并列超出名额时显示整组平分与奖金池封顶",
      "同分的那一组平分剩余名额" in _splitm and "奖金池 150R 封顶" in _splitm, _splitm)
check("实时月榜：平分后的金额按 2 位小数显示在奖金列",
      "33.33R" in _splitm and "50R" in _splitm, _splitm)

# /查询 单人档：NapCat 在/不在两条路径
P_TS = int(datetime.datetime(2099, 11, 10, 8, 0).timestamp())
P_NOW = datetime.datetime(2099, 12, 3, 12, 0)
pf_api = report.render_profile(1001, {"card": "小明", "join_time": P_TS}, "测试群",
                              now=P_NOW)
check("/查询 模板行齐全",
      all(x in pf_api for x in ("成功查询到 1001", "[发言数据]", "[群数据]",
                                "自检测起共计发言句数：50", "总分数获得：5.5",
                                "昵称：小明")), pf_api)
check("/查询 今日行带句数/分数与达上限标注",
      "今日（2099-12-03）有效发言共 50 句，获得 5 分（已抵达今日上限）" in pf_api, pf_api)
check("/查询 本月行读已结算的月总分",
      "本月（2099-12）有效发言共 50 句，获得 5.5 分" in pf_api, pf_api)
check("/查询 入群时间优先用接口的 join_time(精确到分钟)",
      "入群时间：2099-11-10 08:00（NapCat 实时）" in pf_api, pf_api)
check("/查询 拿到群名时不再提示 NapCat 未连接", "NapCat 未连接" not in pf_api, pf_api)
pf_db = report.render_profile(1005, None, "", now=datetime.datetime(2099, 12, 5, 12, 0))
check("/查询 NapCat 不在线时回落库里的入群日期",
      "入群时间：未知" in pf_db and "NapCat 未连接" in pf_db, pf_db)
check("/查询 今日没发言就是 0 句 0 分，本月仍读历史",
      "今日（2099-12-05）有效发言共 0 句，获得 0 分" in pf_db
      and "本月（2099-12）有效发言共 60 句" in pf_db, pf_db)
check("/查询 库里与接口都没有的人明确说查无记录",
      "未查询到 99999 的数据" in report.render_profile(
          99999, None, "", now=datetime.datetime(2099, 12, 5, 12, 0)))
check("日榜未截断32宽度以内的昵称", "昵称长到不行的用户甲乙丙" in d5, d5)
_rows5 = [l for l in d5.splitlines()[1:] if not l.startswith("注:")]


def _col_x(line, needle):
    return report._width(line[: line.index(needle)])


# 定宽表：把每行按列宽切回去，QQ 列必须正好落在同一位置，备注只能在最后
_cut = [_split_fixed(l, report.DAY_WIDTHS) for l in _rows5[1:]]
check("日榜定宽：昵称长短不改变 QQ 列位置",
      len(_cut) == 5 and all(c[2].strip().isdigit() for c, _ in _cut), _rows5)
check("日榜备注列只剩新人/拉新，不参与定宽对齐",
      all(t.strip() == "" or t.startswith(("新人", "拉新")) for _, t in _cut),
      [t for _, t in _cut])
check("日榜备注给新人标出入群日期与邀请人来源",
      next(t for c, t in _cut if c[2].strip() == "1001").startswith(
          "新人12-05入群，邀请人未记到"),
      [t for _, t in _cut])
check("日榜表头与数据行共用同一组列宽",
      _split_fixed(_rows5[0], report.DAY_WIDTHS)[0][2].strip() == "QQ", _rows5[0])
check("月榜同分并列名次(1,2,2,2,5)",
      [l.split()[0] for l in lm.splitlines()[2:]] == ["1", "2", "2", "2", "5"], lm)
check("月榜超长昵称仍等宽",
      len({_fixed_w(l, report.MONTH_WIDTHS) for l in lm.splitlines()[1:]}) == 1, lm)
check("左对齐列贴住列首(修 _pad 只认 left 导致整列右移)",
      report._pad("abc", 10, "l").startswith("abc")
      and report._pad("abc", 10, "left").startswith("abc"))
_short5 = next(l for l in _rows5 if "短名" in l)
check("用户列固定补满到 QQ 昵称最大宽度",
      _col_x(_short5, "短名") == report.COL_RANK + 2
      and _split_fixed(_short5, report.DAY_WIDTHS)[0][1]
      == "短名" + " " * (report.NAME_WIDTH - report._width("短名")), _short5)
check("双向控制符与假空格会从昵称里清掉(它们会让整行列位漂移)",
      report._clean("开学\u1160\u2067~喵\u202d") == "开学 ~喵",
      report._clean("开学\u1160\u2067~喵\u202d"))
check("emoji 会从昵称里清掉(浏览器里它宽度不固定，会把整列推歪)",
      report._clean("求带\U0001F911✨") == "求带",
      report._clean("求带\U0001F911✨"))
check("翻文字工具的反转字母会被清掉",
      report._clean("(suʞɐʍɾʞdd)ʎɹuǝɥ") == "(sudd)u",
      report._clean("(suʞɐʍɾʞdd)ʎɹuǝɥ"))
check("清洗后的昵称宽度=可见字符宽度",
      report._width(report._clean("求带\U0001F911")) == 4
      and report._width(report._clean("AB■cd")) == 4,
      report._width(report._clean("求带\U0001F911")))

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

# ================= DB5b: 榜单备注（新人 / 邀请人 / 拉新得分） =================
print("== DB5b: board notes for newbies and inviters ==")
p5b = make_db("db5b")
conn5b = sqlite3.connect(p5b)
for q, nm in ((3001, "新人甲"), (3002, "邀请人乙"), (3003, "审批人丙"), (3004, "新人丁")):
    conn5b.execute("INSERT INTO user_info(qq, name) VALUES(?,?)", (q, nm))
# 3001: 邀请人自己在窗口内发过 /新人绑定 → 已确认
conn5b.execute(
    "INSERT INTO join_log(qq, group_id, invited_by, join_date, invited_src, join_kind) "
    "VALUES(3001,?,3002,'2099-12-03',2,'invite')", (GROUP,))
conn5b.execute(
    "INSERT INTO join_history(qq, group_id, join_date, invited_by) "
    "VALUES(3001,?,'2099-12-03',3002)", (GROUP,))
# 3004: 管理员审批入群，operator_id 是审批人不是拉他的人
conn5b.execute(
    "INSERT INTO join_log(qq, group_id, invited_by, join_date, invited_src, join_kind) "
    "VALUES(3004,?,3003,'2099-12-10',1,'approve')", (GROUP,))
conn5b.execute(
    "INSERT INTO join_history(qq, group_id, join_date, invited_by) "
    "VALUES(3004,?,'2099-12-10',3003)", (GROUP,))
conn5b.execute(
    "INSERT INTO newbie_bonus(newbie_qq, inviter_qq, awarded_month, bonus) "
    "VALUES(3001,3002,'2099-12',10)")
conn5b.execute(
    "INSERT INTO month_score(qq, month, total, robux, settled) "
    "VALUES(3002,'2099-12',6.0,50,1)")
conn5b.execute(
    "INSERT INTO month_score(qq, month, total, robux, settled) "
    "VALUES(3001,'2099-12',2.0,0,1)")
conn5b.execute(
    "INSERT INTO month_score(qq, month, total, robux, settled) "
    "VALUES(3004,'2099-12',1.0,0,1)")
conn5b.execute("INSERT INTO daily_score VALUES(3002,'2099-12-03',6.0,60)")
conn5b.execute("INSERT INTO daily_score VALUES(3001,'2099-12-03',2.0,20)")
conn5b.execute("INSERT INTO daily_score VALUES(3004,'2099-12-10',1.0,10)")
conn5b.commit()

lm5b = report.render_month_board("2099-12", now=datetime.datetime(2100, 1, 15, 12, 0))
check("已结算月榜有备注列", "备注" in lm5b.splitlines()[1], lm5b)
check("已结算月榜标出已确认的邀请人",
      "新人12-03入群，邀请人 邀请人乙(本人已确认)" in lm5b, lm5b)
check("审批入群的不把审批人写成邀请人，只标待定",
      "新人12-10入群，邀请人待定(管理员放行，绑上才算拉新)" in lm5b
      and "审批人丙" not in lm5b, lm5b)
check("已结算月榜把拉新得分备注在邀请人一行",
      "拉新 +10（新人甲 达标）" in lm5b, lm5b)
check("备注列不参与定宽对齐",
      len({_fixed_w(l, report.MONTH_WIDTHS) for l in lm5b.splitlines()[1:]}) == 1, lm5b)

conn5b.execute("UPDATE month_score SET settled=0 WHERE month='2099-12'")
conn5b.commit()
live5b = report.render_month_board("2099-12", now=datetime.datetime(2099, 12, 20, 12, 0))
check("实时月榜同样带新人/邀请人备注",
      "邀请人 邀请人乙(本人已确认)" in live5b and "备注" in live5b.splitlines()[1], live5b)
check("备注只出现在有入群记录的人那一行",
      sum("新人12-03入群" in l for l in live5b.splitlines()) == 1, live5b)

day5b = report.render_day_board("2099-12-03", now=datetime.datetime(2099, 12, 5, 12, 0))
check("日榜备注显示拉新得分", "拉新 +10（新人甲 达标）" in day5b, day5b)

pf5b = report.render_profile(3001, None, "测试群", now=datetime.datetime(2099, 12, 20, 12, 0))
check("/查询 单人档列出邀请关系与新人加成",
      "邀请关系：邀请人 邀请人乙(本人已确认)" in pf5b
      and "本月按新人计" in pf5b, pf5b)
pf5b2 = report.render_profile(3002, None, "测试群", now=datetime.datetime(2099, 12, 20, 12, 0))
check("/查询 邀请人一行显示本月拉新得分",
      "本月拉新得分 拉新 +10（新人甲 达标）" in pf5b2, pf5b2)
conn5b.close()

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
    "db_path": p6, "group_id": str(GROUP), "no_score_qqs": "9999",
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

# ---- 二次确认：不带 确认 只给预览，预览一行数据都不改 ----
parts, ok = admin_ops.split_confirm("2099-11-20 确认")
check("确认词从参数里摘掉并单独返回", parts == ["2099-11-20"] and ok is True, (parts, ok))
check("不带确认词时 confirmed 为 False",
      admin_ops.split_confirm("2099-11-20") == (["2099-11-20"], False))
check("确认提示原样回显整条命令",
      admin_ops.confirm_hint("重置日", ["2099-11-20"])
      == "未执行。确认无误后原样再发一次: /重置日 2099-11-20 确认")
conn6.execute("DELETE FROM daily_score WHERE date='2099-11-20'")
conn6.commit()
plan = admin_ops.plan_reset_day("2099-11-20", now=NOW6)
left_after_plan = conn6.execute(
    "SELECT COUNT(*) FROM daily_score WHERE date='2099-11-20'"
).fetchone()[0]
check("预览只回显不改数据", "重算" in plan and left_after_plan == 0, (plan, left_after_plan))
check("预览会说明动了哪些表、不受影响的有什么",
      "msg" in plan and "手工调整都不受影响" in plan, plan)

# ---- /扣除：写 score_adjust，重算日分抹不掉，结算与实时月榜都算它 ----
d1 = admin_ops.deduct_score(2001, 0.5, "2099-10", "刷屏", now=NOW6)
adj = conn6.execute(
    "SELECT delta, reason FROM score_adjust WHERE qq=2001 AND month='2099-10'"
).fetchone()
check("扣除写入负 delta 并记下原因",
      "扣除 2001 2099-10 0.5 分完成" in d1 and abs(adj[0] + 0.5) < 1e-9
      and adj[1] == "刷屏", (d1, adj))
check("已结算月份拒绝扣除",
      "已结算" in admin_ops.deduct_score(2001, 1, "2099-11", now=NOW6))
check("不计分名单里的QQ拒绝扣除",
      "不计分" in admin_ops.plan_deduct(9999, 1, "2099-10"))
conn6.execute("INSERT INTO daily_score VALUES(2001,'2099-10-05',2.0,20)")
conn6.commit()
s10 = ms.settle("2099-10", {**MCfg, "top_n": 1})
tot1 = next(r for r in s10["rows"] if r["qq"] == 2001)["total"]
check("结算把扣分计入总分(2.0-0.5=1.5)", abs(tot1 - 1.5) < 1e-9, s10["rows"])
admin_ops.deduct_score(2002, 99, "2099-10", now=NOW6)
s10b = ms.settle("2099-10", {**MCfg, "top_n": 1})
tot2 = next(r for r in s10b["rows"] if r["qq"] == 2002)["total"]
check("扣分不会把总分压成负数", tot2 == 0.0, tot2)
check("扣除预览回显该人该月家底",
      "已手工调整" in admin_ops.plan_deduct(2001, 0.5, "2099-10"),
      admin_ops.plan_deduct(2001, 0.5, "2099-10"))
conn6.execute("INSERT INTO daily_score VALUES(2003,'2100-02-05',1.0,10)")
conn6.commit()
admin_ops.deduct_score(2003, 0.4, "2100-02", now=NOW6)
live_adj = report.render_month_board("2100-02", now=datetime.datetime(2100, 2, 20, 10, 0))
check("实时月榜同样计入手工扣分", "0.6" in live_adj, live_adj)
admin_ops.reset_month("2099-10", now=datetime.date(2099, 11, 25))
still = conn6.execute(
    "SELECT COUNT(*) FROM score_adjust WHERE qq=2001 AND month='2099-10'"
).fetchone()[0]
check("重置月重算日分不会抹掉手工扣分", still == 1, still)
check("重置月预览会提示手工调整不受影响",
      "手工调整都不受影响" in admin_ops.plan_reset_month("2099-10", now=NOW6),
      admin_ops.plan_reset_month("2099-10", now=NOW6))

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


class ApiNotAvailable(Exception):
    """同名复刻 aiocqhttp 的异常：它 `raise ApiNotAvailable` 抛的是类，str() 为空。"""


class FlakyBot(FakeBot):
    """前 fail_first 次调用"没连上"，之后才返回名单——启动竞态的真实形状。"""

    def __init__(self, members, fail_first=1):
        super().__init__(members)
        self._fail = fail_first

    async def call_action(self, action, **kw):
        self.calls.append((action, kw))
        if len(self.calls) <= self._fail:
            raise ApiNotAvailable()
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
asyncio.run(sched.member_sync_job(retry_seconds=()))
check("同步失败不落库且记录错误原因",
      conn7.execute("SELECT COUNT(*) FROM members").fetchone()[0] == 0
      and "failed to fetch" in sched.STATE["sync"]["error"],
      sched.STATE["sync"])
check("空消息异常也打得出不带实例的异常类型",
      "RuntimeError" in sched.STATE["sync"]["error"], sched.STATE["sync"]["error"])
check("NapCat 明确报错时不做无谓重试",
      sched.STATE["sync"]["attempts"] == 1, sched.STATE["sync"])

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

# 入群时间精确到秒：NapCat 名单里的 join_time 不能只留到"日"，
# 插件装之前就进群的人（2025 年进的）也要能显示成 2025 年那一分秒
check("同步把 join_time 按秒存进 members",
      conn7.execute("SELECT join_ts FROM members WHERE qq=3001").fetchone()[0] == JOIN_TS)
check("回填的 join_log 一起带上秒级入群时刻",
      conn7.execute("SELECT join_ts FROM join_log WHERE qq=3001").fetchone()[0] == JOIN_TS)
check("没有 join_time 的成员不写脏时刻",
      conn7.execute("SELECT join_ts FROM members WHERE qq=3002").fetchone()[0] == 0)
conn7.execute("UPDATE join_log SET join_ts=0 WHERE qq=3001")
conn7.commit()
sched.set_context(Ctx([FakePlat(FakeBot(MEMBERS), "napcat")]))
asyncio.run(sched.member_sync_job(SYNC1 + datetime.timedelta(minutes=30)))
check("老记录缺的入群时刻由下一次同步补齐",
      conn7.execute("SELECT join_ts FROM join_log WHERE qq=3001").fetchone()[0] == JOIN_TS)
_pf7 = report.render_profile(3001, None, "测试群",
                            now=datetime.datetime(2099, 11, 20, 12, 0))
check("/查询 用库里的同步时刻，NapCat 不在线也精确到分钟",
      "入群时间：2099-11-10 08:00（群成员同步）" in _pf7, _pf7)
conn7.execute("UPDATE members SET join_ts=0 WHERE qq=3001")
conn7.execute("UPDATE join_log SET join_ts=0 WHERE qq=3001")
conn7.commit()
check("只有日期时退回显示到日",
      "入群时间：2099-11-10（只到日）" in report.render_profile(
          3001, None, "测试群", now=datetime.datetime(2099, 11, 20, 12, 0)))

# 第二次同步：3002 退群 → in_group=0 → 退出扣分名单，但保留历史
sched.set_context(Ctx([FakePlat(FakeBot(MEMBERS[:1]))]))
asyncio.run(sched.member_sync_job(SYNC1 + datetime.timedelta(hours=1)))
check("退群成员标记in_group=0",
      conn7.execute(
          "SELECT in_group FROM members WHERE qq=3002"
      ).fetchone()[0] == 0)
check("退群成员不再进扣分名单",
      3002 not in ds.roster_with_join_dates(conn7, GROUP))

# self_id 路由：定时任务没有事件上下文，aiocqhttp 只认带 self_id 的那条反向 WS
check("群消息事件里记下了机器人自己的号", mh.get_self_id() == 9999, mh.get_self_id())


class SelfIdBot(FakeBot):
    """只有带 self_id 的调用才回名单——真实反向 WS 有多条连接时的形状。"""

    async def call_action(self, action, **kw):
        self.calls.append((action, kw))
        if not kw.get("self_id"):
            raise ApiNotAvailable()
        return self._members


SIB = SelfIdBot([{"user_id": 3004, "card": "路由", "nickname": "n3004",
                  "role": "member", "join_time": JOIN_TS}])
sched.set_context(Ctx([FakePlat(SIB, "napcat")]))
asyncio.run(sched.member_sync_job(SYNC1 + datetime.timedelta(hours=3)))
st = sched.STATE["sync"]
check("后台任务靠 self_id 选中连接并同步成功",
      st["count"] == 1 and st["routing"] == "self_id"
      and SIB.calls[0][1].get("self_id") == "9999", (st, SIB.calls[:1]))
check("self_id 排在参数候选最前",
      sched._member_call_variants(SIB)[0] == {"self_id": "9999"},
      sched._member_call_variants(SIB))


class WsrBot(FakeBot):
    _wsr_api_clients = {"7777": object()}


mh._SELF_ID = None
check("没记下 self_id 时改用连接表里的",
      sched._member_call_variants(WsrBot())[0] == {"self_id": "7777"},
      sched._member_call_variants(WsrBot()))
check("两者都没有时退回裸调用(靠唯一连接)",
      sched._member_call_variants(FakeBot()) == [{}, {"no_cache": True}],
      sched._member_call_variants(FakeBot()))
mh.remember_self_id(types.SimpleNamespace())
mh.remember_self_id(types.SimpleNamespace(get_self_id=lambda: "abc"))
check("没这个接口或号不是数字时安静忽略", mh.get_self_id() is None, mh.get_self_id())

# 启动竞态：第一轮"没连上"，退避后第二轮连上了 → 必须落库
# 上面已把 self_id 清空，每轮固定 2 次调用，fail_first=2 正好跨过第一轮
FLAKY = FlakyBot([{"user_id": 3003, "card": "迟到的人", "nickname": "n3003",
                   "role": "member", "join_time": JOIN_TS}], fail_first=2)
sched.set_context(Ctx([FakePlat(FLAKY)]))
asyncio.run(sched.member_sync_job(SYNC1 + datetime.timedelta(hours=2),
                                  retry_seconds=(0,)))
st = sched.STATE["sync"]
check("NapCat 晚连上时自动退避重试直到成功",
      st["attempts"] == 2 and st["count"] == 1 and not st["error"], st)
check("重试成功后名单落库",
      conn7.execute("SELECT qq, nickname FROM members WHERE qq=3003").fetchall()
      == [(3003, "迟到的人")], conn7.execute("SELECT qq FROM members").fetchall())
check("空消息异常被写成异常类型名",
      sched._describe_error(ApiNotAvailable()) == "ApiNotAvailable"
      and sched._describe_error(RuntimeError("boom")) == "RuntimeError boom",
      sched._describe_error(ApiNotAvailable()))
check("/同步 预览说明只动名单不动分",
      "members" in admin_ops.plan_sync() and "不改任何人的分数" in admin_ops.plan_sync(),
      admin_ops.plan_sync())
check("/同步 结果回显人数与尝试次数",
      "1 人" in admin_ops.sync_result(True, {"count": 1, "attempts": 2, "platform": "x"})
      and "尝试 1 次" in admin_ops.sync_result(False, {"error": "e", "attempts": 1}),
      admin_ops.sync_result(True, {"count": 1, "attempts": 2, "platform": "x"}))

sched.set_context(Ctx([]))
asyncio.run(sched.member_sync_job(retry_seconds=()))
check("无可用平台时安全跳过", "没有可用" in sched.STATE["sync"]["error"]
      or "call_action" in sched.STATE["sync"]["error"], sched.STATE["sync"])


class ProfileBot(FakeBot):
    """按 action 分别回群成员资料与群资料，并记下每次调用的参数。"""

    async def call_action(self, action, **kw):
        self.calls.append((action, kw))
        if action == "get_group_member_info":
            return {"user_id": kw.get("user_id"), "card": "新人甲", "nickname": "n3001",
                    "join_time": JOIN_TS, "level": "80", "title": "", "role": "member"}
        if action == "get_group_info":
            return {"group_name": "测试群"}
        return None


PB = ProfileBot()
sched.set_context(Ctx([FakePlat(PB, "napcat")]))
INFO, GNAME = asyncio.run(sched.fetch_profile(3001))
check("/查询 用 get_group_member_info 拿入群时间、get_group_info 拿群名",
      INFO and int(INFO["join_time"]) == JOIN_TS and GNAME == "测试群", (INFO, GNAME))
check("查询参数带群号与QQ，no_cache 只有一份(重复关键字会 TypeError)",
      all(k.get("group_id") == GROUP and k.get("user_id") == 3001
          and list(k).count("no_cache") == 1
          for a, k in PB.calls if a == "get_group_member_info"), PB.calls)


class DeadBot(FakeBot):
    pass


sched.set_context(Ctx([FakePlat(DeadBot(err="not connected"))]))
INFO2, GNAME2 = asyncio.run(sched.fetch_profile(3001))
check("NapCat 没连上时 /查询 回落到库里的快照而不是报错",
      INFO2 is None and GNAME2 == "", (INFO2, GNAME2))
sched.set_context(Ctx([]))
INFO3, _ = asyncio.run(sched.fetch_profile(3001))
check("没有 OneBot 平台时查询安全返回空", INFO3 is None, INFO3)

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
    check("title_reward 占位配置已彻底移除",
          "title_reward" not in schema_text
          and "| title_reward" not in pathlib.Path(HERE, "README.md").read_text("utf-8"),
          schema_text[-80:])
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

# ================= DB10: 邀请关系人工确认（/新人绑定 窗口） =================
print("== DB10: inviter confirm window ==")
p10 = make_db("db10")
conn10 = sqlite3.connect(p10)
_CFG10 = {
    **_CFG_BASE, "db_path": p10, "group_id": str(GROUP),
    "invite_bind": {"enabled": True, "window_minutes": 5},
}
set_plugin_config(_CFG10)
for q, nm in ((5001, "邀请人一"), (5002, "被绑新人"), (5005, "真正拉人的"),
              (5009, "审批管理员")):
    conn10.execute("INSERT INTO user_info(qq, name) VALUES(?,?)", (q, nm))
conn10.commit()

T10 = int(datetime.datetime(2099, 12, 1, 10, 0).timestamp())


def _join10(newbie, oper, kind, at):
    raw = {"post_type": "notice", "notice_type": "group_increase",
           "group_id": GROUP, "user_id": newbie, "operator_id": oper,
           "sub_type": kind}
    return asyncio.run(
        mh.handle_group_join(FakeEvent(newbie, "", raw=raw), _now=at)
    )


def _jl(qq):
    return conn10.execute(
        "SELECT COALESCE(invited_by,0), COALESCE(invited_src,0), "
        "COALESCE(join_kind,'') FROM join_log WHERE qq=?", (qq,)
    ).fetchone()


def _pb(qq):
    return conn10.execute(
        "SELECT COALESCE(detected_inviter,0), join_ts, COALESCE(bound_ts,0) "
        "FROM pending_bind WHERE newbie_qq=?", (qq,)
    ).fetchone()


check("入群事件开出待确认窗口并记下检测到的邀请人",
      _join10(5002, 5001, "invite", T10) is None
      and _pb(5002) == (5001, T10, 0) and _jl(5002) == (5001, 1, "invite"),
      (_pb(5002), _jl(5002)))
check("入群一律不返回要说的话：机器人在群里不出声",
      _join10(5016, 5001, "invite", T10) is None)
_jh_seg = mh_src.split("async def handle_group_join")[1]
check("入群处理里没有任何发群的分支",
      "plain_result" not in _jh_seg and "prompt" not in _jh_seg.split("finally")[0],
      _jh_seg[:120])
ok10 = admin_ops.bind_inviter(5002, 5001, _now=T10 + 60)
check("邀请人绑定成功并标成人工确认",
      "绑定成功" in ok10 and "人工确认" in ok10 and _jl(5002)[1] == 2, ok10)
check("绑定后带昵称回显，窗口标记已结掉",
      "被绑新人(5002) 的邀请人 = 邀请人一(5001)" in ok10 and _pb(5002)[2] == T10 + 60,
      (ok10, _pb(5002)))
again = admin_ops.bind_inviter(5002, 5001, _now=T10 + 90)
check("重复绑定被挡掉", "已在" in again and "不再重复绑定" in again, again)

_join10(5003, 5001, "invite", T10)
late = admin_ops.bind_inviter(5003, 5001, _now=T10 + 5 * 60 + 1)
check("超出窗口不再受理，自动检测的结果保留",
      "超时" in late and _jl(5003) == (5001, 1, "invite"), (late, _jl(5003)))

_join10(5004, 5009, "approve", T10)
check("审批入群：流水里留着谁放行的，但窗口里当成没检测到邀请人",
      _jl(5004) == (5009, 1, "approve") and _pb(5004) == (0, T10, 0),
      (_jl(5004), _pb(5004)))
check("审批入群也不在群里喊人，只把窗口开成没检测到",
      _join10(5015, 5009, "approve", T10) is None and _pb(5015) == (0, T10, 0),
      _pb(5015))
rebind = admin_ops.bind_inviter(5004, 5005, _now=T10 + 30)
check("审批入群没有真邀请人，真正拉人的来绑就认",
      "绑定成功" in rebind and "管理员不算邀请人" in rebind and _jl(5004)[0] == 5005,
      (rebind, _jl(5004)))
check("绑定回复里说清之前放行的是谁",
      "放行他的管理员 审批管理员(5009)" in rebind and "真正拉人的(5005)" in rebind, rebind)
_jh4 = conn10.execute(
    "SELECT invited_by FROM join_history WHERE qq=5004 ORDER BY id DESC LIMIT 1"
).fetchone()[0]
check("绑定同时更新入群流水，拉新奖励按新邀请人发", _jh4 == 5005, _jh4)
_join10(5014, 5001, "invite", T10)
grab = admin_ops.bind_inviter(5014, 5005, _now=T10 + 20)
check("自动检测到了邀请人就不许别人改绑，检测结果原样保留",
      "不接受改绑" in grab and _jl(5014) == (5001, 1, "invite")
      and _pb(5014)[2] == 0, (grab, _jl(5014)))
check("挡抢绑时告诉对方该谁自己来发",
      "邀请人一(5001) 本人发 /新人绑定 5014" in grab, grab)
decl = admin_ops.bind_inviter(5014, 5005, _now=T10 + 40, declared=True)
check("管理员在后台代填能覆盖自动检测，来源标成代填",
      "绑定成功" in decl and "覆盖了自动检测到的 邀请人一(5001)" in decl
      and _jl(5014) == (5005, 3, "invite"), (decl, _jl(5014)))
selfh = admin_ops.bind_inviter(5002, 5002, _now=T10 + 10)
check("不能把自己绑成自己的邀请人", "自己" in selfh, selfh)
norp = admin_ops.bind_inviter(9999, 5001, _now=T10 + 10)
check("库里没这个人时直说绑不了", "没有 9999 的入群记录" in norp, norp)
conn10.execute("DELETE FROM pending_bind WHERE newbie_qq=5003")
conn10.commit()
nopb = admin_ops.bind_inviter(5003, 5001, _now=T10 + 10)
check("入群那会儿插件不在线时解释为什么没有窗口",
      "插件不在线" in nopb and "没有待确认窗口" in nopb, nopb)

_join10(5012, 0, "link", T10)
linkb = admin_ops.bind_inviter(5012, 5013, _now=T10 + 20)
check("链接/二维码入群也开窗口，谁认领记在谁头上",
      "绑定成功" in linkb and "没检测到邀请人" in linkb and _jl(5012)[0] == 5013,
      (linkb, _jl(5012)))
check("认领后来源标成人工确认，备注不再写无邀请人", _jl(5012)[1] == 2, _jl(5012))

_join10(5006, 5001, "invite", T10)
_join10(5006, 5001, "invite", T10 + 700)  # 退了又进: 窗口重开
back = admin_ops.bind_inviter(5006, 5001, _now=T10 + 720)
check("退了又进的人没有邀请奖励，绑定也挡掉", "退了又进" in back, back)
check("重开窗口把 bound_ts 归零", _pb(5006)[2] == 0, _pb(5006))

set_plugin_config({**_CFG10, "no_score_qqs": str(5001)})
bots = admin_ops.bind_inviter(5007, 5001, _now=T10 + 10)
check("不计分名单里的号（群内机器人）不接受绑定", "不计分名单" in bots, bots)
set_plugin_config(_CFG10)

conn10.execute(
    "INSERT INTO pending_bind(newbie_qq, group_id, detected_inviter, join_ts, bound_ts) "
    "VALUES(7777,?,5001,?,0)", (GROUP, T10 - 90000))
conn10.commit()
_join10(5008, 5001, "invite", T10)
check("一天前没绑上的旧窗口随下一次入群清掉",
      conn10.execute("SELECT COUNT(*) FROM pending_bind WHERE newbie_qq=7777")
      .fetchone()[0] == 0)
check("入群永远不会返回要说的话，配置页也没有\"群内提示\"这一项了",
      _join10(5010, 5001, "invite", T10) is None
      and '"prompt"' not in schema_text.split('"invite_bind"')[1].split('"report"')[0])
_gn_body = main_src.split("async def group_notice")[1].split("@filter", 1)[0]
check("group_notice 只入库：没有任何回话分支",
      "yield" not in _gn_body and "plain_result" not in _gn_body, _gn_body[:120])
set_plugin_config({**_CFG10, "invite_bind": {"enabled": False}})
off10 = admin_ops.bind_inviter(5011, 5001, _now=T10 + 10)
check("关掉后指令直接说未启用，不碰库", "未启用" in off10 and _pb(5011) is None, off10)
set_plugin_config(_CFG10)

check("备注只写邀请人是谁，不解释入群方式",
      report._inviter_text(5001, 2, "invite", {}) == "邀请人 5001(本人已确认)"
      and report._inviter_text(5001, 1, "invite", {}) == "邀请人 5001(未确认)"
      and report._inviter_text(5003, 1, "approve", {})
      == "邀请人待定(管理员放行，绑上才算拉新)"
      and report._inviter_text(0, 0, "approve", {})
      == "邀请人待定(管理员放行，绑上才算拉新)"
      and report._inviter_text(0, 0, "link", {}) == "自己搜群号入群，无邀请人"
      and report._inviter_text(5001, 3, "invite", {}) == "邀请人 5001(管理员代填)"
      and "未记到" in report._inviter_text(0, 0, "", {}))
check("参数不是一条 QQ 时只回用法",
      admin_ops.plan_bind("")[2].startswith("用法")
      and admin_ops.plan_bind("abc")[0] is None
      and admin_ops.plan_bind("123")[0] is None
      and admin_ops.plan_bind("123456789。")[0] == 123456789
      and admin_ops.plan_bind("123456789")[1] == 0)
check("后台的写法多带一个邀请人QQ，第二个参数写坏时整条不受理",
      admin_ops.plan_bind("123456789 987654321") == (123456789, 987654321, "")
      and admin_ops.plan_bind("123456789 abc")[0] is None
      and admin_ops.plan_bind("123456789 99")[0] is None)
class _Cmd10(FakeEvent):
    """带平台名的假事件：QQ 群与 AstrBot 后台两条路径分开走一遍。"""

    def __init__(self, uid, text, platform):
        super().__init__(uid, text)
        self._platform = platform

    def get_platform_name(self):
        return self._platform

    def plain_result(self, text):
        return ("plain", text)


def _bind_out(uid, text, platform):
    async def run():
        return [r async for r in bot.cmd_bind_inviter(_Cmd10(uid, text, platform))]
    return asyncio.run(run())


_join10(60001, 5001, "invite", int(time.time()) - 60)
check("群里抢注别人拉的新人：不出声，也不改库",
      _bind_out(5005, "/新人绑定 60001", "aiocqhttp") == []
      and _jl(60001) == (5001, 1, "invite") and _pb(60001)[2] == 0, _jl(60001))
check("群里多带一个 QQ 也不算管理员代填，照样只是抢绑",
      _bind_out(5005, "/新人绑定 60001 60003", "aiocqhttp") == []
      and _jl(60001) == (5001, 1, "invite"), _jl(60001))
_one = _bind_out(5005, "/新人绑定 60001", "webchat")
check("后台只写新人QQ时不受理，让它补上邀请人QQ",
      len(_one) == 1 and "邀请人QQ" in _one[0][1] and _jl(60001)[1] == 1, _one)
_two = _bind_out(5005, "/新人绑定 60001 60003", "webchat")
check("后台两个参数=管理员代填，能覆盖自动检测并标成代填",
      len(_two) == 1 and "绑定成功" in _two[0][1] and "管理员代填" in _two[0][1]
      and _jl(60001) == (60003, 3, "invite"), _two)
_join10(60002, 5009, "approve", int(time.time()) - 60)
check("审批入群没有真邀请人，本人在群里来绑照样认",
      _bind_out(5005, "/新人绑定 60002", "aiocqhttp") == []
      and _jl(60002) == (5005, 2, "approve"), _jl(60002))
check("群里参数写错也不出声", _bind_out(5005, "/新人绑定 abc", "aiocqhttp") == [])
diag10 = report.render_diag(now=datetime.datetime(2099, 12, 1, 12, 0))
check("自检列出待确认窗口与已人工确认人数",
      "待确认" in diag10 and "人工确认" in diag10 and "邀请确认窗口" in diag10, diag10)
bind_src = pathlib.Path(HERE, "main.py").read_text("utf-8")
check("新人绑定是群内指令(不加仅后台闸门)",
      "cmd_bind_inviter" in bind_src and "新人绑定" in bind_src)
_bind_seg = bind_src.split("async def cmd_bind_inviter")[1].split("@filter.command")[0]
check("群里的绑定结果只写日志，不回话",
      "webchat = _is_webchat(event)" in _bind_seg
      and _bind_seg.index("if webchat:") < _bind_seg.index("plain_result")
      and "logger.info" in _bind_seg[_bind_seg.index("plain_result"):], _bind_seg)
check("只有后台那条路径是管理员代填，群里发的一律按本人自证",
      _bind_seg.index("declared=True") < _bind_seg.index("admin_ops.bind_inviter(newbie, binder)"),
      _bind_seg)
check("WebUI 里发绑定仍然回全文(便于管理员测试)",
      "yield event.plain_result(text)" in _bind_seg, _bind_seg)
conn10.close()

# 汇总
print(f"\n结果: {PASS} 通过, {FAIL} 失败")
import shutil

shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)
