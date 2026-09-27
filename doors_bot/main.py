"""
doors_bot - AstrBot 插件入口 (v4.x)

机器人在 QQ 群里不出声：不主动发任何消息，也不回普通聊天。
- 静默记录目标群有效发言（纯文本、去重防刷屏、120秒多人窗口计分）
- 每日计分/扣分、月度结算、Robux 榜单全部写入数据库与 pay_list 文件
后台查看与运维：只在 AstrBot WebUI 聊天窗里用下面的指令（QQ 里发这些不会有响应）。
群里唯一能用的指令是 /新人绑定：它照旧改库，但机器人不回话，结果只写日志，
管理员在后台看 /查询 或月榜备注（"本人已确认"）核对。

指令一览（日期支持 今天/昨天/前天/DD/YYYY-MM-DD，月份支持 本月/上月/M/YYYY-MM）:
  查看: /日榜 [日期] /昨日榜 /月榜 [月份] /上月榜 /自检 /查询 <QQ>
        /日榜 不带参数就是当天实时榜，/月榜 不带参数就是当月榜
        /查询 出一个人在本插件里的发言档、入群时间与邀请关系
  运维: /重置日 [日期] /重置月 [月份] /删除 <QQ> [月份] /扣除 <QQ> <分数> [月份] [原因]
        /结算 [月份] /补跑 /同步
        除 /同步 外每一条都要二次确认：先发一次得到"将要改什么"的预览，
        再发一次同样内容并在末尾加 确认 才真正执行。
        /同步 只拉成员名单、不动任何分数，发一次就跑。
  群内: /新人绑定 <新人QQ> 邀请人认领自己拉进群的新人，入群后限时有效；
        在群里发它机器人不出声，结果只进日志，后台用 /查询 或看月榜备注核对
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
from .runtime_config import (
    get_group_id,
    get_invite_bind_config,
    set_plugin_config,
)


def _argv(event: AstrMessageEvent, name: str) -> str:
    """取指令名之后的参数。

    AstrBot 只在消息以 wake_prefix 开头或有 @ 时剥离前缀，ChatUI 里发 "/日榜 x"
    时 "/" 不会被剥离，所以按指令名定位，两种写法都能拿到参数。
    """
    text = event.message_str or ""
    idx = text.find(name)
    return text[idx + len(name):].strip() if idx >= 0 else text.strip()


def _is_webchat(event: AstrMessageEvent) -> bool:
    """这条指令是不是在 AstrBot 后台聊天窗里发的。"""
    try:
        return str(event.get_platform_name() or "") == "webchat"
    except Exception:
        return False


class DoorsBot(Star):
    """QQ 群活跃统计与 Robux 月度结算（群内静默，榜单与运维在 WebUI 后台）"""

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
        # 首轮同步会自己退避重试：AstrBot 刚起来时 NapCat 的反向 WS 常常还没连上
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

    # AstrBot 把"带 group_id 的 OneBot 通知"转成 GROUP_MESSAGE(aiocqhttp 适配器
    # _convert_handle_notice_event)，所以只挂 OTHER_MESSAGE 会永远收不到入群通知。
    # 这里放开到 ALL，由 handle_group_join 自己按 post_type/notice_type 过滤。
    @filter.event_message_type(filter.EventMessageType.ALL)
    async def group_notice(self, event: AstrMessageEvent):
        try:
            prompt = await handle_group_join(event)
        except Exception:
            logger.exception("[doors_bot] 入群事件处理异常")
            return
        # 只有配置页打开"进群时催一句"才会走到这里，默认不发任何消息。
        if prompt:
            mins = get_invite_bind_config()["window_minutes"]
            newbie = prompt["newbie"]
            if prompt.get("approve"):
                # 管理员放行那次事件里带的人是审批人，不是拉人的人，所以不点名
                text = (
                    f"{newbie} 进群时是管理员放行的，看不出谁拉的他："
                    f"拉他进群的人请在 {mins} 分钟内发 /新人绑定 {newbie}，"
                    "没人认领就不算拉新。"
                )
            else:
                text = (
                    f"{prompt['inviter']} 像是把 {newbie} 拉进群的人："
                    f"{mins} 分钟内发 /新人绑定 {newbie} 就能确认这层邀请关系，"
                    "不理会照自动检测的结果记账。"
                )
            yield event.plain_result(text)

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

    @filter.command("昨日榜", alias={"/昨日榜"},
                     desc="看昨天的发言日榜（只读，不需要确认）")
    @filter.platform_adapter_type(filter.PlatformAdapterType.WEBCHAT)
    async def rep_yesterday(self, event: AstrMessageEvent):
        day = (datetime.date.today() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        yield event.plain_result(report.render_day_board(day, fenced=True))

    @filter.command("上月榜", alias={"/上月榜"},
                     desc="看上个月的发言月榜与每人应发 Robux（只读，不需要确认）")
    @filter.platform_adapter_type(filter.PlatformAdapterType.WEBCHAT)
    async def rep_last_month(self, event: AstrMessageEvent):
        last = (datetime.date.today().replace(day=1) - datetime.timedelta(days=1))
        yield event.plain_result(report.render_month_board(last.strftime("%Y-%m"), fenced=True))

    @filter.command("日榜", alias={"/日榜"},
                     desc="看某天的发言日榜：不带参数是当天实时，支持 昨天/前天/2026-09-24（只读）")
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

    @filter.command("月榜", alias={"/月榜"},
                     desc="看某月的积分榜：不带参数是当月实时预估，支持 上月/2026-09（只读）")
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

    @filter.command("自检", alias={"/自检"},
                     desc="出体检报告：库路径、各表行数、漏算日期、成员同步结果、任务下次触发时间（只读）")
    @filter.platform_adapter_type(filter.PlatformAdapterType.WEBCHAT)
    async def rep_diag(self, event: AstrMessageEvent):
        yield event.plain_result(report.render_diag(fenced=True))

    @filter.command("查询", alias={"/查询"},
                     desc="查某个人的档：今日/本月/累计发言与得分、入群时间、游号、QQ 会员（只读）")
    @filter.platform_adapter_type(filter.PlatformAdapterType.WEBCHAT)
    async def rep_profile(self, event: AstrMessageEvent):
        """/查询 <QQ>：单人发言档、入群时间、游号与 QQ 会员。

        入群时间现调 NapCat 的 get_group_member_info，会员状态再补一次
        get_stranger_info；NapCat 没连上就回落到 members 快照（只到日），
        会员一栏显示未知。
        """
        parts, _ = admin_ops.split_confirm(_argv(event, "查询"))
        if not parts or not parts[0].isdigit() or len(parts[0]) < 5:
            yield event.plain_result("用法: /查询 <QQ号>，例: /查询 123456789")
            return
        qq = int(parts[0])
        info, group_name = await sched.fetch_profile(qq)
        yield event.plain_result(
            report.render_profile(qq, info, group_name, fenced=True)
        )

    # ---- 邀请关系确认（群内也能用：邀请人本人不会去开后台） ----

    @filter.command("新人绑定", alias={"/新人绑定"},
                    desc="邀请人认领自己拉进群的新人：群里 /新人绑定 <新人QQ>（绑定者=发送者本人，"
                         "不回话只写日志）；后台 /新人绑定 <新人QQ> <邀请人QQ> 由管理员代填")
    async def cmd_bind_inviter(self, event: AstrMessageEvent):
        """/新人绑定 <新人QQ> [邀请人QQ]：把"这个人是谁拉进来的"确认下来。

        群里的写法只带新人 QQ，绑定者就是发这条的人自己（第二个参数不理）；
        后台聊天窗的发送者不是 QQ 号，所以那边只认参数里写出来的邀请人 QQ。
        机器人在群里不出声：QQ 里发这条只写日志、只改库，绑没绑上后台用 /查询 或
        月榜备注（"邀请人 X(本人已确认)"）核对。WebUI 里发才会回这一段文字。
        """
        newbie, inviter, hint = admin_ops.plan_bind(_argv(event, "新人绑定"))
        webchat = _is_webchat(event)
        if newbie is None:
            text = hint
        elif webchat:
            text = (
                admin_ops.bind_inviter(newbie, inviter, declared=True)
                if inviter
                else f"后台要写成 /{admin_ops.BIND_CMD} <新人QQ> <邀请人QQ>；"
                     "群里由邀请人本人发，只写新人 QQ。"
            )
        else:
            try:
                binder = int(event.get_sender_id() or 0)
            except (TypeError, ValueError):
                binder = 0
            text = (
                admin_ops.bind_inviter(newbie, binder)
                if binder > 0
                else "取不到发送者的 QQ 号，没法确认是谁在绑定。"
            )
        if webchat:
            yield event.plain_result(text)
            return
        logger.info(f"[doors_bot] 群内 /新人绑定: {' '.join(text.split())}")


    # ---- 运维（会改数据，一律需要 确认；QQ 平台不会命中） ----

    @filter.command("重置日", alias={"/重置日"},
                     desc="按 msg 原始发言重算某天的日分（要加 确认 才执行）")
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

    @filter.command("重置月", alias={"/重置月"},
                     desc="重算某月每一天的日分（要加 确认 才执行）")
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

    @filter.command("删除", alias={"/删除"},
                     desc="删掉某人某月的日分与月榜行，先回显被删的行（要加 确认 才执行）")
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

    @filter.command("扣除", alias={"/扣除"},
                     desc="手工扣某人某月的分，记进 score_adjust，重算抹不掉（要加 确认 才执行）")
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

    @filter.command("结算", alias={"/结算"},
                     desc="立刻结算指定月份并生成发放名单文件（要加 确认 才执行）")
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

    @filter.command("同步", alias={"/同步"},
                     desc="手动向 NapCat 拉一次全群成员名单：只动名单，不动任何分数")
    @filter.platform_adapter_type(filter.PlatformAdapterType.WEBCHAT)
    async def cmd_member_sync(self, event: AstrMessageEvent):
        """/同步 [确认]：立刻向 NapCat 拉一次全群成员，不用等下一个整点。"""
        parts, ok = admin_ops.split_confirm(_argv(event, "同步"))
        if not ok:
            yield event.plain_result(
                f"{admin_ops.plan_sync()}\n{admin_ops.confirm_hint('同步', parts)}"
            )
            return
        # 手动触发时人还等着回复，只短促重试两下；后台任务用完整的退避节奏
        done = await sched.member_sync_job(retry_seconds=(2, 5))
        yield event.plain_result(
            self._op(f"同步 {' '.join(parts)}",
                     admin_ops.sync_result(done, sched.STATE["sync"]))
        )

    @filter.command("补跑", alias={"/补跑"},
                     desc="补算有发言却没落日分的日期（要加 确认 才执行）")
    @filter.platform_adapter_type(filter.PlatformAdapterType.WEBCHAT)
    async def cmd_catchup(self, event: AstrMessageEvent):
        parts, ok = admin_ops.split_confirm(_argv(event, "补跑"))
        yield event.plain_result(
            self._guarded(
                "补跑", parts, admin_ops.plan_catchup(),
                admin_ops.manual_catchup, ok,
            )
        )
