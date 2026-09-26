# doors_bot_data —— 运行数据目录

插件运行时的落盘位置（与插件目录同级、但在其之外，删插件或换版本都不会丢数据）：

```
<AstrBot 数据根>/data/plugins/
├── doors_bot/          插件代码
└── doors_bot_data/     本目录：doors.db + pay_list_YYYY-MM.txt
```

- `doors.db`：SQLite 库，含发言记录、日分/月分、成员快照与入群流水，**都是群成员的个人数据**
- `pay_list_YYYY-MM.txt`：每月结算导出的 Robux 发放名单

两者都不进版本库（见根目录 `.gitignore`）。备份或迁移时**先停掉 AstrBot 再复制**；
本插件绝不自动搬库或删除旧库，路径可用配置页 `db_path` 指到别处。
