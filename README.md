# 树莓派股票 / Crypto 提醒程序

Python 3.11+，仅标准库，SQLite 保存状态。systemd 每小时启动一次，读取文件、检查、发送提醒后退出。程序只读取行情，不下单。

## 第一版行为

- 每次读取指定的 `watchlist.md`（一个 TOML 代码块）或 `.txt`（直接写 TOML）。不扫描其他文件，不用 AI 解释自由文本。路径固定，避免读错文件。
- 默认目标上下 5%，含边界；首次运行已在范围内也提醒。连续停留在范围内不重复。
- 检测到离开后重新准备提醒；再次进入受默认 24 小时冷却限制。冷却期间进入并一直停留，会在冷却结束后的首次检查提醒。
- SQLite 保存状态，重启保留。修改目标、范围、标的或数据源会重置规则状态；note 不会。删除规则保留历史，复用旧 id 会恢复旧状态，建议新规则使用新 id。
- 美股仅在 Alpaca clock 确认正常交易时段时检查；节假日、提前收盘由 clock 处理。Crypto 每天 24 小时检查。
- 一小时采样可能错过两次检查之间的短暂触价。重启后不补发停机期间的历史触价。
- 过期行情、历史不足、接口错误均不触发价格提醒；记录日志，其他规则继续。通知失败不消耗提醒状态，下次运行重新尝试。
- 外部通知和本地数据库不能原子提交：服务已接收但网络超时/进程崩溃时，下次可能重复发送，无法保证严格恰好一次。

## 数据来源和均线

第一版统一使用 Alpaca HTTP API，需要设置 Alpaca **Paper 账户 API key**（clock 使用 paper API）。无需开通自动交易功能；本程序不调用交易接口。

- 美股默认 IEX，实时数据不是全市场合并行情。`stock_feed = "sip"` 需要相应订阅权限。
- Crypto 默认 `crypto_location = "us"`（Alpaca）；也允许文档列出的 `us-1` / `eu-1`（Kraken）。历史长度和账户权限需实际验证。
- 不是 TradingView 数据接口，不承诺与 TradingView 完全一致。请比对图表交易所、交易对、复权及周期边界。
- `target = 100` 是固定价格；`target = "SMA_200W"` 是 200 周简单均线；`"SMA_200D"` 是 200 日简单均线。支持 1–999 个周期，不支持 EMA。
- 使用供应商周/日 K 线收盘价，美股拆股复权、不调整分红。美股周期边界按纽约时间，Crypto 按 UTC，周一开始。排除当前未完成周期；周线到下周一才纳入上一周，不在周五即时切换。
- 周线检查连续性和最新一周，缺少足够历史则拒绝计算。新上市币或不同 crypto 数据源不一定有 200 周记录，不会用少量数据冒充。
- 缓存历史 K 线最多 24 小时；跨周期立即重取。定期完整刷新所需窗口以更新复权。缓存期间公司行动调整可能尚未反映。

官方接口参考（2026-09-06 查阅）：

- [美股历史 K 线](https://docs.alpaca.markets/us/v1.4.2/reference/stockbars)
- [Crypto 历史 K 线](https://docs.alpaca.markets/us/reference/cryptobars-1)
- [行情覆盖和权限](https://docs.alpaca.markets/us/docs/market-data-faq)

## 树莓派安装

使用 Raspberry Pi OS Bookworm 或更新版本，确认 `python3 --version` 至少为 3.11。将本项目复制到树莓派某个目录，进入该目录再执行：

```bash
sudo apt update
sudo apt install -y python3 tzdata ca-certificates
sudo useradd --system --user-group --home-dir /var/lib/stockwatch --no-create-home --shell /usr/sbin/nologin stockwatch
sudo install -d /opt/stockwatch
sudo install -d -m 0750 -o root -g stockwatch /etc/stockwatch
sudo install -m 0644 monitor.py /opt/stockwatch/monitor.py
sudo install -m 0640 -o root -g stockwatch watchlist.md /etc/stockwatch/watchlist.md
sudo install -m 0600 deploy/stockwatch.env.example /etc/stockwatch.env
sudo install -m 0644 deploy/stockwatch.service /etc/systemd/system/stockwatch.service
sudo install -m 0644 deploy/stockwatch.timer /etc/systemd/system/stockwatch.timer
```

如果 stockwatch 用户已经存在，跳过 useradd。再次部署代码只复制 monitor.py，**不要覆盖已修改的配置和密钥文件**。

编辑目标和凭据：

```bash
sudo nano /etc/stockwatch/watchlist.md
sudo nano /etc/stockwatch.env
```

通知二选一：

- Telegram：用官方 @BotFather 创建 bot，将 token 填进 `TELEGRAM_BOT_TOKEN`。先从手机向自己的 bot 发送 `/start`，通过 Telegram Bot API 的 `getUpdates` 获取自己的 `message.chat.id`，填 `TELEGRAM_CHAT_ID`。不要把 token 放进监控文件或共享截图。
- Pushover：安装并注册手机应用，创建 API application，将应用 token 和个人 user key 填入示例配置对应项；`NOTIFY_CHANNEL=pushover`。

## 启用前验证

离线检查格式，不调用任何 API：

```bash
python3 monitor.py --rules watchlist.md --validate
python3 -m unittest -v
```

在树莓派用临时 systemd 服务加载同一份凭据做实时试运行：只打印潜在提醒，不发送，也不改变提醒状态（会缓存行情）。

```bash
sudo systemd-run --unit=stockwatch-preview --wait --pipe --collect \
  -p User=stockwatch -p Group=stockwatch \
  -p EnvironmentFile=/etc/stockwatch.env \
  -p StateDirectory=stockwatch \
  /usr/bin/python3 /opt/stockwatch/monitor.py \
  --rules /etc/stockwatch/watchlist.md \
  --db /var/lib/stockwatch/monitor.sqlite3 --dry-run
```

确认后启用定时任务，并立即检查一次（符合条件会发通知）：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now stockwatch.timer
sudo systemctl start stockwatch.service
systemctl list-timers stockwatch.timer
journalctl -u stockwatch.service -n 100 --no-pager
```

`Type=oneshot` 完成后显示 inactive 是正常的；查看 timer 是否 active。开机约两分钟检查一次，随后每个整点附近运行。`Persistent=true` 会在重新启用时补一次错过的计划，不补所有小时。失败时服务返回非零，下个整点重新尝试；没有无限重启循环。

首次验证手机推送可临时添加一个目标接近当前价的独立测试规则，手动启动服务，确认收到后删除测试规则。`--dry-run` 不验证推送凭据。

## 修改和维护

- 修改 `/etc/stockwatch/watchlist.md` 后，下一次运行生效，无需重启 timer。文件有语法错误则本轮整份停止并写日志，修正后下轮恢复。建议先 `--validate`。
- 如使用 `.txt`，复制 `watchlist.example.txt` 到 `/etc/stockwatch/watchlist.txt`，并将服务 `ExecStart` 中的 `--rules` 路径改为它；运行 `sudo systemctl daemon-reload`。
- 检查间隔由 timer 决定。例如每两小时改为 `OnCalendar=*-*-* 00/2:00:00`，然后 `sudo systemctl daemon-reload` 和 `sudo systemctl restart stockwatch.timer`。
- SQLite 位于 `/var/lib/stockwatch/monitor.sqlite3`，配置和数据库都要保留。备份时先停止 timer 并等待 service 结束，再复制数据库，完成后启动 timer。
- 查看日志：`journalctl -u stockwatch.service --since today`。本版尚无独立外部掉线监控；树莓派断电、断网或通知渠道故障不能靠同一程序可靠通知手机。
- 停止：`sudo systemctl disable --now stockwatch.timer`；若本轮仍在运行，另执行 `sudo systemctl stop stockwatch.service`。

## 验证范围

测试覆盖配置校验、200 周 SMA、缺失周线、重启去重、再次进入和冷却、通知失败、dry-run、历史分页、过期行情和美股接口失败隔离。未配置真实 API 凭据时无法验证账户数据权限、200 周历史覆盖或实际手机送达。systemd 文件需要在树莓派 Linux 上完成实际启用验证。
