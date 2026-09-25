"""
doors_bot - AstrBot 插件入口 (v4.x)

QQ 群内纯静默：不回复、不主动发送任何消息。
- 静默记录目标群有效发言（纯文本、去重防刷屏、120秒多人窗口计分）
- 每日计分/扣分、月度结算、Robux 榜单全部写入数据库与 pay_list 文件
后台查看与运维：仅在 AstrBot WebUI 聊天窗使用下面的指令（QQ 里发这些内容不会有任何响应），
也可直接读 SQLite 库 / pay_list_YYYY-MM.txt。

指令一览（日期支持 今天/昨天/前天/DD/YYYY-MM-DD，月份支持 本月/上月/M/YYYY-MM）:
  查看: /日榜 [日期] /昨日榜 /月榜 [月份] /上月榜 /自检
        /日榜 不带参数就是当天实时榜，/月榜 不带参数就是当月榜
  运维: /重置日 [日期] /重置月 [月份] /删除 <QQ> [月份] /扣除 <QQ> <分数> [月份] [原因]
        /结算 [月份] /补跑
        以上每一条都必须二次确认：先发一次得到"将要改什么"的预览，
        再发一次同样内容并在末尾加 确认 才真正执行。
"""

from __future__ import annotations

import datetime

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Star

from . import admin_ops, report
from . import scheduler as sched
from .init_db import init
from .message_handler import handle_group_join, handle_group_message
from .runtime_config import get_group_id, set_plugin_config


def _argv(event: AstrMessageEvent, name: str) -> str:
    """取指令名之后的参数。

    AstrBot 只在消息以 wake_prefix 开头或有 @ 时剥离前缀，ChatUI 里发 "/日榜 x"
    时 "/" 不会被剥离，所以按指令名定位，两种写法都能拿到参数。
    """
    text = event.message_str or ""
    idx = text.find(name)
    return text[idx + len(name):].strip() if idx >= 0 else text.strip()


class DoorsBot(Star):
    """QQ群活跃统计与Robux奖励系统（纯后台统计，不在群里发言）"""

    def __init__(self, context, config: dict | None = None):
        super().__init__(context)
        self.config = dict(config or {})
        set_plugin_config(self.config)
        init()
        if get_group_id() <= 0:
            logger.warning(
                "[doors_bot] 未配置目标群 group_id：发言不会入库，榜单会是空的"
            )
        logger.info("Doors Bot 已加载（静默模式）")

    async def initialize(self):
        sched.set_context(self.context)
        sched.start()
        # 启动后先补跑漏算日期、再同步一次群成员（都只写库，不发言）
        self.context.register_task(
            sched.catchup_job(), "doors_bot 启动补跑漏算日期"
        )
        self.context.register_task(
            sched.member_sync_job(), "doors_bot 群成员首轮同步"
        )

    async def terminate(self):
        sched.shutdown()

    # ---------- 事件（只读记录，永不回复） ----------

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def group_message(self, event: AstrMessageEvent):
        try:
            await handle_group_message(event)
        except Exception:
            logger.exception("[doors_bot] 消息统计异常")

    @filter.event_message_type(filter.EventMessageType.OTHER_MESSAGE)
    async def group_notice(self, event: AstrMessageEvent):
        try:
            await handle_group_join(event)
        except Exception:
            logger.exception("[doors_bot] 入群事件处理异常")

    # ---------- 后台指令：仅 AstrBot WebUI 平台可触发 ----------
    # 注意: AstrBot 只在消息以 wake_prefix(默认 "." "//") 开头或有 @ 时剥离前缀。
    # ChatUI 里发 "/日榜" 时 "/" 不会被剥离，因此把带斜杠的写法登记为别名，
    # 裸写、.前缀、//前缀、/前缀四种都能命中。QQ 平台被 WEBCHAT 过滤器挡住。

    def _today(self) -> str:
        return datetime.date.today().strftime("%Y-%m-%d")

    def _op(self, name: str, text: str) -> str:
        """改数据的指令统一留一行日志，便于事后核对谁在什么时候动了账。"""
        logger.info(f"[doors_bot] 后台操作 {name}: {text.splitlines()[0]}")
        return text

    def _guarded(
        self, cmd: str, args: list[str], plan: str, run, confirmed: bool
    ) -> str:
        """写操作两段式：不带 确认 只回显"要改什么"，带了才真执行。"""
        if not confirmed:
            return f"{plan}\n{admin_ops.confirm_hint(cmd, args)}"
        return self._op(f"{cmd} {' '.join(args)}", run())

    # ---- 查看（只读，不需要确认） ----

    @filter.command("昨日榜", alias={"/昨日榜"})
    @filter.platform_adapter_type(filter.PlatformAdapterType.WEBCHAT)
    async def rep_yesterday(self, event: AstrMessageEvent):
        day = (datetime.date.today() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        yield event.plain_result(report.render_day_board(day, fenced=True))

    @filter.command("上月榜", alias={"/上月榜"})
    @filter.platform_adapter_type(filter.PlatformAdapterType.WEBCHAT)
    async def rep_last_month(self, event: AstrMessageEvent):
        last = (datetime.date.today().replace(day=1) - datetime.timedelta(days=1))
        yield event.plain_result(report.render_month_board(last.strftime("%Y-%m"), fenced=True))

    @filter.command("日榜", alias={"/日榜"})
    @filter.platform_adapter_type(filter.PlatformAdapterType.WEBCHAT)
    async def rep_day(self, event: AstrMessageEvent):
        """不带参数 = 当天实时榜；带日期 = 该日榜（支持 今天/昨天/前天/DD）。"""
        parts, _ = admin_ops.split_confirm(_argv(event, "日榜"))
        day = admin_ops.parse_date(" ".join(parts))
        if not day:
            yield event.plain_result(
                f"无法识别日期 {parts!r}，示例: /日榜 2026-09-24 或 /日榜 昨天"
            )
            return
        yield event.plain_result(report.render_day_board(day, fenced=True))

    @filter.command("月榜", alias={"/月榜"})
    @filter.platform_adapter_type(filter.PlatformAdapterType.WEBCHAT)
    async def rep_month(self, event: AstrMessageEvent):
        """不带参数 = 当月榜；带月份 = 该月榜（支持 本月/上月/M/9）。"""
        parts, _ = admin_ops.split_confirm(_argv(event, "月榜"))
        month = admin_ops.parse_month(" ".join(parts))
        if not month:
            yield event.plain_result(
                f"无法识别月份 {parts!r}，示例: /月榜 2026-09 或 /月榜 上月"
            )
            return
        yield event.plain_result(report.render_month_board(month, fenced=True))

    @filter.command("自检", alias={"/自检"})
    @filter.platform_adapter_type(filter.PlatformAdapterType.WEBCHAT)
    async def rep_diag(self, event: AstrMessageEvent):
        yield event.plain_result(report.render_diag(fenced=True))

    # ---- 运维（会改数据，一律需要 确认；QQ 平台不会命中） ----

    @filter.command("重置日", alias={"/重置日"})
    @filter.platform_adapter_type(filter.PlatformAdapterType.WEBCHAT)
    async def cmd_reset_day(self, event: AstrMessageEvent):
        parts, ok = admin_ops.split_confirm(_argv(event, "重置日"))
        day = admin_ops.parse_date(" ".join(parts))
        if not day:
            yield event.plain_result(f"无法识别日期 {parts!r}，示例: /重置日 昨天")
            return
        yield event.plain_result(
            self._guarded(
                "重置日", [day], admin_ops.plan_reset_day(day),
                lambda: admin_ops.reset_day(day), ok,
            )
        )

    @filter.command("重置月", alias={"/重置月"})
    @filter.platform_adapter_type(filter.PlatformAdapterType.WEBCHAT)
    async def cmd_reset_month(self, event: AstrMessageEvent):
        parts, ok = admin_ops.split_confirm(_argv(event, "重置月"))
        month = admin_ops.parse_month(" ".join(parts))
        if not month:
            yield event.plain_result(f"无法识别月份 {parts!r}，示例: /重置月 2026-09")
            return
        yield event.plain_result(
            self._guarded(
                "重置月", [month], admin_ops.plan_reset_month(month),
                lambda: admin_ops.reset_month(month), ok,
            )
        )

    @filter.command("删除", alias={"/删除"})
    @filter.platform_adapter_type(filter.PlatformAdapterType.WEBCHAT)
    async def cmd_delete_row(self, event: AstrMessageEvent):
        parts, ok = admin_ops.split_confirm(_argv(event, "删除"))
        if not parts or not parts[0].isdigit():
            yield event.plain_result("用法: /删除 <QQ> [月份]，例: /删除 123456789 2026-09")
            return
        month = admin_ops.parse_month(" ".join(parts[1:]))
        if not month:
            yield event.plain_result(f"无法识别月份 {parts[1:]!r}，示例: /删除 123456789 上月")
            return
        qq = int(parts[0])
        yield event.plain_result(
            self._guarded(
                "删除", [str(qq), month], admin_ops.plan_delete(qq, month),
                lambda: admin_ops.delete_board_row(qq, month), ok,
            )
        )

    @filter.command("扣除", alias={"/扣除"})
    @filter.platform_adapter_type(filter.PlatformAdapterType.WEBCHAT)
    async def cmd_deduct(self, event: AstrMessageEvent):
        """/扣除 <QQ> <分数> [月份] [原因]：按月记一笔手工扣分，结算时计入总分。"""
        parts, ok = admin_ops.split_confirm(_argv(event, "扣除"))
        if len(parts) < 2 or not parts[0].isdigit():
            yield event.plain_result(
                "用法: /扣除 <QQ> <分数> [月份] [原因]，例: /扣除 123456789 2 2026-09 刷屏"
            )
            return
        try:
            points = float(parts[1])
        except ValueError:
            yield event.plain_result(f"分数不是数字: {parts[1]!r}")
            return
        if points <= 0:
            yield event.plain_result("扣除的分数要写正数（要补分就用负数以外的手段，本指令只扣分）")
            return
        qq = int(parts[0])
        month = admin_ops.parse_month(parts[2]) if len(parts) > 2 else None
        reason = " ".join(parts[3:] if month else parts[2:])
        month = month or self._today()[:7]
        args = [str(qq), f"{points:g}", month] + ([reason] if reason else [])
        yield event.plain_result(
            self._guarded(
                "扣除", args, admin_ops.plan_deduct(qq, points, month),
                lambda: admin_ops.deduct_score(qq, points, month, reason), ok,
            )
        )

    @filter.command("结算", alias={"/结算"})
    @filter.platform_adapter_type(filter.PlatformAdapterType.WEBCHAT)
    async def cmd_settle(self, event: AstrMessageEvent):
        parts, ok = admin_ops.split_confirm(_argv(event, "结算"))
        month = admin_ops.parse_month(" ".join(parts))
        if not month:
            yield event.plain_result(f"无法识别月份 {parts!r}，示例: /结算 2026-08")
            return
        yield event.plain_result(
            self._guarded(
                "结算", [month], admin_ops.plan_settle(month),
                lambda: admin_ops.settle_month(month, force=ok), ok,
            )
        )

    @filter.command("补跑", alias={"/补跑"})
    @filter.platform_adapter_type(filter.PlatformAdapterType.WEBCHAT)
    async def cmd_catchup(self, event: AstrMessageEvent):
        parts, ok = admin_ops.split_confirm(_argv(event, "补跑"))
        yield event.plain_result(
            self._guarded(
                "补跑", parts, admin_ops.plan_catchup(),
                admin_ops.manual_catchup, ok,
            )
        )
