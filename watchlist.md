# 股票和 Crypto 监控

每次运行重新读取下面的 TOML。检查间隔由 systemd timer 控制，默认每小时。
价格仅为示例，请替换。target 可以填数字，或 "SMA_200W" / "SMA_200D"。
band_percent = 5 表示目标价上下 5%。id 必须唯一，修改目标会重置该规则的提醒状态。

```toml
[settings]
cooldown_hours = 24
max_quote_age_minutes = 20
stock_feed = "iex"
crypto_location = "us"

[[watch]]
id = "aapl-target"
market = "stock"
symbol = "AAPL"
target = 100
band_percent = 5
note = "接近我的观察价位"

[[watch]]
id = "btc-weekly"
market = "crypto"
symbol = "BTC/USD"
target = "SMA_200W"
band_percent = 5
note = "观察200周均线附近"
```
