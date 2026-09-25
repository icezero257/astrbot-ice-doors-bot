# doors_bot

AstrBot 插件：QQ 群活跃统计与 Robux 奖励结算。**机器人在群内绝对静默**，只默默记录发言，
所有榜单查询与运维操作都只能在 AstrBot 后台聊天窗（WebUI）里做。

- 计分：120 秒窗口内至少 2 人发言才算有效发言；防复读、过滤无意义短消息、未发言扣分、
  新人加成、邀请奖励、手工扣分
- 结算：按月自动结算，奖金池封顶 `名额数 × 单份`（默认 10×50=500R），并列共享名次、
  并列组超出名额时整组平分剩余名额
- 榜单：日榜/月榜（实时与已结算两种）、单人档 `/查询 <QQ>`、每月发放名单文件
- 数据：SQLite，落在插件目录之外的 `data/plugins/doors_bot_data/`，删插件不丢库

## 安装

1. 把 `doors_bot/` 整个目录放进 AstrBot 的 `data/plugins/` 下
2. 重启 AstrBot，在插件配置页填 **`group_id`（目标群号，必填，留空则一条都不统计）**
   和 `no_score_qqs`（群里的机器人号，发言完全不入库）
3. 平台需为 OneBot v11（NapCat / aiocqhttp）；群成员同步与 `/查询` 的入群时间依赖它，
   连不上时插件仍能计分，只是那几栏显示"未知"

详细规则、13 条后台指令与二次确认口径、库迁移与故障排查见
[`doors_bot/README.md`](doors_bot/README.md)。

## 发布

`make_release.py` 把开发目录同步进 `doors_bot/`、清空配置页里的个人默认值、
扫描全仓确保没有现网群号/QQ 残留，再打出平铺的 `release/doors_bot_v<版本>.zip`
（Release 附件）。数据与发放名单在 `doors_bot_data/`，同样不入库。
