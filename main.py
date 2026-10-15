import time
import requests
import pandas as pd
from datetime import datetime

# ==================== 配置区 ====================
SERVERCHAN_SENDKEY = "SCT440554TBR9un4ZZPUOu6r28LJvNDpY8"

SYMBOL = "BTC-USDT"          # 欧意格式
CHECK_INTERVAL = 20
MIN_BUY_SELL_RATIO = 2.5
NEAR_LOWER_15M = 0.35
NEAR_LOWER_5M = 0.25
NEAR_LOWER_3M = 0.20
SIGNAL_COOLDOWN = 300

def send_wechat(title: str, content: str):
    url = f"https://sctapi.ftqq.com/{SERVERCHAN_SENDKEY}.send"
    data = {"title": title, "desp": content}
    try:
        resp = requests.post(url, data=data, timeout=10)
        print("微信提醒发送成功" if resp.json().get("code") == 0 else f"发送失败: {resp.text}")
    except Exception as e:
        print(f"微信发送异常: {e}")

def get_okx_orderbook():
    try:
        url = f"https://www.okx.com/api/v5/market/books?instId={SYMBOL}&sz=10"
        data = requests.get(url, timeout=5).json()
        if data.get("code") != "0" or not data.get("data"):
            print("欧意订单簿获取失败")
            return 0, 0
        bids = sum(float(p) * float(q) for p, q, _, _ in data["data"][0]["bids"])
        asks = sum(float(p) * float(q) for p, q, _, _ in data["data"][0]["asks"])
        return bids, asks
    except Exception as e:
        print(f"欧意订单簿错误: {e}")
        return 0, 0

def get_okx_klines(interval="15m", limit=50):
    # 欧意时间周期对应：1m, 3m, 5m, 15m, 1H 等
    bar_map = {"15m": "15m", "5m": "5m", "3m": "3m"}
    bar = bar_map.get(interval, "15m")
    try:
        url = f"https://www.okx.com/api/v5/market/candles?instId={SYMBOL}&bar={bar}&limit={limit}"
        data = requests.get(url, timeout=5).json()
        if data.get("code") != "0" or not data.get("data"):
            raise ValueError("K线数据异常")
        # 欧意返回的是倒序，需要反转
        rows = data["data"][::-1]
        df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "vol", "volCcy", "volCcyQuote", "confirm"])
        df["close"] = df["close"].astype(float)
        return df
    except Exception as e:
        print(f"K线获取失败: {e}")
        return None

def calculate_bollinger(df, period=20, std=2):
    df = df.copy()
    df["ma"] = df["close"].rolling(period).mean()
    df["std"] = df["close"].rolling(period).std()
    df["upper"] = df["ma"] + std * df["std"]
    df["lower"] = df["ma"] - std * df["std"]
    return df

def get_bb_info(interval):
    df = get_okx_klines(interval)
    if df is None or len(df) < 20:
        return None
    df = calculate_bollinger(df)
    latest = df.iloc[-1]
    price = latest["close"]
    lower = latest["lower"]
    mid = latest["ma"]
    dist = (price - lower) / price * 100 if price > 0 else 999
    return {"price": price, "lower": lower, "mid": mid, "dist": dist}

print("=" * 50)
print("事件合约信号监控已启动（仅使用欧意数据）")
print("=" * 50)

last_signal_time = 0

while True:
    try:
        ok_bids, ok_asks = get_okx_orderbook()
        ok_ratio = ok_bids / ok_asks if ok_asks > 0 else 0
        orderbook_ok = ok_ratio >= MIN_BUY_SELL_RATIO

        bb_15 = get_bb_info("15m")
        bb_5  = get_bb_info("5m")
        bb_3  = get_bb_info("3m")

        if bb_15 is None:
            print("布林带数据获取失败，跳过本轮")
            time.sleep(CHECK_INTERVAL)
            continue

        price = bb_15["price"]
        near_15 = bb_15["dist"] < NEAR_LOWER_15M and price < bb_15["mid"]
        near_short = False
        if bb_5:
            near_short = near_short or (bb_5["dist"] < NEAR_LOWER_5M)
        if bb_3:
            near_short = near_short or (bb_3["dist"] < NEAR_LOWER_3M)

        signal = orderbook_ok and near_15 and near_short

        now = datetime.now().strftime("%H:%M:%S")
        print(f"[{now}] 价格:{price:.1f} | 15m距离下轨:{bb_15['dist']:.2f}% | 欧意买/卖比:{ok_ratio:.2f}")

        if signal:
            current_time = time.time()
            if current_time - last_signal_time > SIGNAL_COOLDOWN:
                title = "【事件合约做多信号】"
                content = f"""
**时间**：{now}
**当前价格**：{price:.1f}

**距离下轨**：
- 15分钟：{bb_15['dist']:.2f}%
- 5分钟：{bb_5['dist']:.2f}% if bb_5 else 'N/A'
- 3分钟：{bb_3['dist']:.2f}% if bb_3 else 'N/A'

**欧意买/卖比**：{ok_ratio:.2f}

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
        print(f"主循环错误: {e}")
        time.sleep(10)

    time.sleep(CHECK_INTERVAL)
