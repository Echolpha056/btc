import time
import requests
import pandas as pd
from datetime import datetime

# ==================== 配置区 ====================
SERVERCHAN_SENDKEY = "SCT440554TBR9un4ZZPUOu6r28LJvNDpY8"

SYMBOL = "BTCUSDT"
BB_PERIOD = 20
BB_STD = 2
ORDERBOOK_DEPTH = 10
CHECK_INTERVAL = 20
MIN_BUY_SELL_RATIO = 2.5

NEAR_LOWER_15M = 0.35
NEAR_LOWER_5M  = 0.25
NEAR_LOWER_3M  = 0.20
SIGNAL_COOLDOWN = 300

def send_wechat(title: str, content: str):
    if not SERVERCHAN_SENDKEY:
        print("未配置SendKey，跳过推送")
        return
    url = f"https://sctapi.ftqq.com/{SERVERCHAN_SENDKEY}.send"
    data = {"title": title, "desp": content}
    try:
        resp = requests.post(url, data=data, timeout=10)
        print("微信提醒发送成功" if resp.json().get("code") == 0 else f"发送失败: {resp.text}")
    except Exception as e:
        print(f"微信发送异常: {e}")

def get_binance_orderbook():
    url = f"https://api.binance.com/api/v3/depth?symbol={SYMBOL}&limit={ORDERBOOK_DEPTH}"
    data = requests.get(url, timeout=5).json()
    bids = sum(float(p) * float(q) for p, q in data["bids"])
    asks = sum(float(p) * float(q) for p, q in data["asks"])
    return bids, asks

def get_okx_orderbook():
    url = f"https://www.okx.com/api/v5/market/books?instId={SYMBOL}&sz={ORDERBOOK_DEPTH}"
    data = requests.get(url, timeout=5).json()
    if data.get("code") != "0":
        return 0, 0
    bids = sum(float(p) * float(q) for p, q, _, _ in data["data"][0]["bids"])
    asks = sum(float(p) * float(q) for p, q, _, _ in data["data"][0]["asks"])
    return bids, asks

def get_binance_klines(interval="15m", limit=50):
    url = f"https://api.binance.com/api/v3/klines?symbol={SYMBOL}&interval={interval}&limit={limit}"
    data = requests.get(url, timeout=5).json()
    df = pd.DataFrame(data, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_buy_base",
        "taker_buy_quote", "ignore"
    ])
    df["close"] = df["close"].astype(float)
    df["volume"] = df["volume"].astype(float)
    return df

def calculate_bollinger(df, period=20, std=2):
    df = df.copy()
    df["ma"] = df["close"].rolling(period).mean()
    df["std"] = df["close"].rolling(period).std()
    df["upper"] = df["ma"] + std * df["std"]
    df["lower"] = df["ma"] - std * df["std"]
    return df

def get_bb_info(interval):
    df = get_binance_klines(interval)
    df = calculate_bollinger(df)
    latest = df.iloc[-1]
    price = latest["close"]
    lower = latest["lower"]
    mid = latest["ma"]
    dist = (price - lower) / price * 100 if price > 0 else 999
    return {"price": price, "lower": lower, "mid": mid, "dist": dist}

print("=" * 50)
print("事件合约信号监控已启动")
print("=" * 50)

last_signal_time = 0

while True:
    try:
        bn_bids, bn_asks = get_binance_orderbook()
        ok_bids, ok_asks = get_okx_orderbook()
        bn_ratio = bn_bids / bn_asks if bn_asks > 0 else 0
        ok_ratio = ok_bids / ok_asks if ok_asks > 0 else 0
        orderbook_ok = bn_ratio >= MIN_BUY_SELL_RATIO and ok_ratio >= MIN_BUY_SELL_RATIO

        bb_15 = get_bb_info("15m")
        bb_5  = get_bb_info("5m")
        bb_3  = get_bb_info("3m")
        price = bb_15["price"]

        near_15 = bb_15["dist"] < NEAR_LOWER_15M and price < bb_15["mid"]
        near_short = (bb_5["dist"] < NEAR_LOWER_5M) or (bb_3["dist"] < NEAR_LOWER_3M)
        signal = orderbook_ok and near_15 and near_short

        now = datetime.now().strftime("%H:%M:%S")
        print(f"[{now}] 价格:{price:.1f} | 15m距离下轨:{bb_15['dist']:.2f}% | 币安:{bn_ratio:.2f} | 欧意:{ok_ratio:.2f}")

        if signal:
            current_time = time.time()
            if current_time - last_signal_time > SIGNAL_COOLDOWN:
                title = "【事件合约做多信号】"
                content = f"""
**时间**：{now}
**当前价格**：{price:.1f}

**距离下轨**：
- 15分钟：{bb_15['dist']:.2f}%
- 5分钟：{bb_5['dist']:.2f}%
- 3分钟：{bb_3['dist']:.2f}%

**订单簿**：
- 币安买/卖比：{bn_ratio:.2f}
- 欧意买/卖比：{ok_ratio:.2f}

建议：最小仓位开【看涨】事件合约（推荐 Gate 5分钟）
"""
                send_wechat(title, content)
                print(">>> 信号已推送到微信！")
                last_signal_time = current_time
            else:
                print("（信号冷却中）")
        else:
            print("暂无信号")

    except Exception as e:
        print(f"错误: {e}")
        time.sleep(10)

    time.sleep(CHECK_INTERVAL)
