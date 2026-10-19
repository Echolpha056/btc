import time
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timezone
import os

# ==================== 配置区 ====================
SYMBOL = "BTCUSDT"
INITIAL_CAPITAL = 1000.0
ORDER_SIZE = 10.0
BB_PERIOD = 20
BB_STD = 2
SERVERCHAN_SENDKEY = "SCT440554TBR9un4ZZPUOu6r28LJvNDpY8"
EVENT_DURATION = 3          # 事件时长（分钟）
SIGNAL_COOLDOWN = 180

# ==================== 全局状态 ====================
capital = INITIAL_CAPITAL
last_signal_time = 0
trade_log = []

# ==================== 工具函数 ====================
def get_binance_klines(interval, limit=50):
    url = "https://api.binance.com/api/v3/klines"
    params = {"symbol": SYMBOL, "interval": interval, "limit": limit}
    r = requests.get(url, params=params, timeout=10)
    data = r.json()
    df = pd.DataFrame(data, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_buy_base",
        "taker_buy_quote", "ignore"
    ])
    df["close"] = df["close"].astype(float)
    df["volume"] = df["volume"].astype(float)
    return df

def bollinger_bands(df, period=20, std=2):
    close = df["close"]
    ma = close.rolling(period).mean()
    std_dev = close.rolling(period).std()
    upper = ma + std * std_dev
    lower = ma - std * std_dev
    return ma.iloc[-1], upper.iloc[-1], lower.iloc[-1]

def get_orderbook_strength(exchange="binance"):
    if exchange == "binance":
        url = "https://api.binance.com/api/v3/depth"
        params = {"symbol": SYMBOL, "limit": 10}
        r = requests.get(url, params=params, timeout=5)
        data = r.json()
        bid_vol = sum(float(b[1]) for b in data["bids"][:5])
        ask_vol = sum(float(a[1]) for a in data["asks"][:5])
    else:
        url = "https://www.okx.com/api/v5/market/books"
        params = {"instId": "BTC-USDT", "sz": 10}
        r = requests.get(url, params=params, timeout=5)
        data = r.json()["data"][0]
        bid_vol = sum(float(b[1]) for b in data["bids"][:5])
        ask_vol = sum(float(a[1]) for a in data["asks"][:5])
    
    if ask_vol == 0:
        return 999
    return bid_vol / ask_vol

def is_bb_flat(ma, upper, lower, threshold=0.0015):
    width = (upper - lower) / ma
    return width < threshold

def send_wechat(title, content):
    url = f"https://sctapi.ftqq.com/{SERVERCHAN_SENDKEY}.send"
    data = {
        "title": title,
        "desp": content
    }
    try:
        requests.post(url, data=data, timeout=8)
    except Exception as e:
        print(f"推送失败: {e}")

# ==================== 信号逻辑 ====================
def check_signal():
    global last_signal_time
    
    now = time.time()
    if now - last_signal_time < SIGNAL_COOLDOWN:
        return None, "冷却中"
    
    try:
        df_1m = get_binance_klines("1m")
        df_3m = get_binance_klines("3m")
        df_5m = get_binance_klines("5m")
        df_15m = get_binance_klines("15m")
    except Exception as e:
        return None, f"获取K线失败: {e}"
    
    ma1, up1, low1 = bollinger_bands(df_1m)
    ma3, up3, low3 = bollinger_bands(df_3m)
    ma5, up5, low5 = bollinger_bands(df_5m)
    ma15, up15, low15 = bollinger_bands(df_15m)
    
    price = df_1m["close"].iloc[-1]
    
    flat_score = sum([
        is_bb_flat(ma3, up3, low3),
        is_bb_flat(ma5, up5, low5),
        is_bb_flat(ma15, up15, low15)
    ])
    
    try:
        binance_strength = get_orderbook_strength("binance")
        okx_strength = get_orderbook_strength("okx")
    except Exception as e:
        return None, f"盘口获取失败: {e}"
    
    vol_1m = df_1m["volume"].iloc[-3:].mean()
    vol_prev = df_1m["volume"].iloc[-6:-3].mean()
    vol_up = vol_1m > vol_prev * 1.08
    
    # ========== 做多条件 ==========
    near_15_lower = price <= low15 * 1.0018
    near_15_mid   = abs(price - ma15) / ma15 < 0.0013
    not_near_upper = price < up15 * 0.9975
    
    near_3_mid = abs(price - ma3) / ma3 < 0.0009
    near_5_mid = abs(price - ma5) / ma5 < 0.0009
    not_chase_long = not (near_3_mid or near_5_mid) or price > max(ma3, ma5) * 1.0006
    
    long_ok = (
        (near_15_lower or near_15_mid) and
        not_near_upper and
        flat_score >= 2 and
        binance_strength > 1.25 and
        okx_strength > 1.25 and
        vol_up and
        not_chase_long
    )
    
    # ========== 做空条件 ==========
    near_15_upper = price >= up15 * 0.9982
    near_15_mid_s = abs(price - ma15) / ma15 < 0.0013
    not_near_lower = price > low15 * 1.0025
    
    not_chase_short = not (near_3_mid or near_5_mid) or price < min(ma3, ma5) * 0.9994
    
    short_ok = (
        (near_15_upper or near_15_mid_s) and
        not_near_lower and
        flat_score >= 2 and
        binance_strength < 0.80 and
        okx_strength < 0.80 and
        vol_up and
        not_chase_short
    )
    
    if long_ok:
        last_signal_time = now
        reason = (
            f"【做多信号】\n"
            f"价格: {price:.1f}\n"
            f"15分中轨: {ma15:.1f} 下轨: {low15:.1f}\n"
            f"币安买/卖: {binance_strength:.2f}  欧易: {okx_strength:.2f}\n"
            f"布林带走平数: {flat_score}"
        )
        return "LONG", reason
    
    if short_ok:
        last_signal_time = now
        reason = (
            f"【做空信号】\n"
            f"价格: {price:.1f}\n"
            f"15分中轨: {ma15:.1f} 上轨: {up15:.1f}\n"
            f"币安买/卖: {binance_strength:.2f}  欧易: {okx_strength:.2f}\n"
            f"布林带走平数: {flat_score}"
        )
        return "SHORT", reason
    
    return None, "无信号"

# ==================== 事件模拟 ====================
def simulate_event(direction="LONG", duration_min=3):
    global capital
    
    if capital < ORDER_SIZE:
        capital = INITIAL_CAPITAL
        send_wechat("资金重置", f"本金已输光，重置为 {INITIAL_CAPITAL}U")
    
    entry_price = float(get_binance_klines("1m", 1)["close"].iloc[-1])
    start_time = datetime.now(timezone.utc)
    
    direction_cn = "做多" if direction == "LONG" else "做空"
    send_wechat(
        f"【{direction_cn}信号】{duration_min}分钟事件",
        f"方向: {direction_cn}\n"
        f"入场价: {entry_price:.1f}\n"
        f"仓位: {ORDER_SIZE}U\n"
        f"当前本金: {capital:.1f}U\n"
        f"等待结果中..."
    )
    
    time.sleep(duration_min * 60 + 8)
    
    exit_price = float(get_binance_klines("1m", 1)["close"].iloc[-1])
    
    if direction == "LONG":
        win = exit_price > entry_price
    else:
        win = exit_price < entry_price
    
    # ========== 盈亏规则 ==========
    # 赢：80%利润 + 10U本金（净赚8U）
    # 输：直接输掉10U本金
    if win:
        profit = ORDER_SIZE * 0.80          # 80%利润 = 8U
        capital += profit                   # 只加利润，本金本来就在
        result = "赢"
        result_detail = f"净赚 {profit:.1f}U"
    else:
        capital -= ORDER_SIZE               # 全输10U
        result = "输"
        result_detail = f"输掉 {ORDER_SIZE}U"
    
    log = {
        "time": start_time.strftime("%Y-%m-%d %H:%M"),
        "direction": direction,
        "entry": entry_price,
        "exit": exit_price,
        "result": result,
        "capital": round(capital, 2)
    }
    trade_log.append(log)
    
    send_wechat(
        f"事件结果: {result}（{direction_cn}）",
        f"入场: {entry_price:.1f}\n"
        f"出场: {exit_price:.1f}\n"
        f"结果: {result}（{result_detail}）\n"
        f"当前本金: {capital:.1f}U"
    )
    
    return log

# ==================== 主循环 ====================
def main():
    print("多空信号系统启动...")
    send_wechat("系统上线", f"事件合约多空信号系统已启动\n初始本金: {INITIAL_CAPITAL}U\n事件时长: {EVENT_DURATION}分钟\n赢：80%利润+本金  输：全输")
    
    while True:
        try:
            direction, reason = check_signal()
            if direction:
                print(f"[{datetime.now()}] 触发信号\n{reason}")
                simulate_event(direction=direction, duration_min=EVENT_DURATION)
            else:
                print(f"[{datetime.now()}] {reason}")
            
            time.sleep(15)
            
        except Exception as e:
            print(f"主循环错误: {e}")
            time.sleep(30)

if __name__ == "__main__":
    main()
