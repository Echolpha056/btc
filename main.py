import time
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timezone
from collections import defaultdict

# ==================== 配置区 ====================
SYMBOL = "BTC-USDT"
BB_PERIOD = 20
BB_STD = 2
SERVERCHAN_SENDKEY = "SCT440554TBR9un4ZZPUOu6r28LJvNDpY8"
SIGNAL_COOLDOWN = 120          # 冷却时间缩短到2分钟

# ==================== 全局状态 ====================
last_signal_time = 0
stats = {
    "3m": {"win": 0, "total": 0},
    "5m": {"win": 0, "total": 0}
}

# ==================== 工具函数（仅OKX） ====================
def get_okx_klines(interval, limit=50):
    url = "https://www.okx.com/api/v5/market/candles"
    params = {"instId": SYMBOL, "bar": interval, "limit": str(limit)}
    r = requests.get(url, params=params, timeout=10)
    data = r.json()
    
    if data.get("code") != "0" or not data.get("data"):
        raise Exception(f"OKX K线获取失败: {data}")
    
    df = pd.DataFrame(data["data"], columns=[
        "ts", "open", "high", "low", "close", "vol", "volCcy", "volCcyQuote", "confirm"
    ])
    df = df.iloc[::-1].reset_index(drop=True)
    df["close"] = df["close"].astype(float)
    df["volume"] = df["vol"].astype(float)
    return df

def bollinger_bands(df, period=20, std=2):
    close = df["close"]
    ma = close.rolling(period).mean()
    std_dev = close.rolling(period).std()
    upper = ma + std * std_dev
    lower = ma - std * std_dev
    return ma.iloc[-1], upper.iloc[-1], lower.iloc[-1]

def get_okx_orderbook_strength():
    url = "https://www.okx.com/api/v5/market/books"
    params = {"instId": SYMBOL, "sz": 10}
    r = requests.get(url, params=params, timeout=5)
    data = r.json()
    
    if data.get("code") != "0" or not data.get("data"):
        raise Exception("OKX盘口获取失败")
    
    book = data["data"][0]
    bid_vol = sum(float(b[1]) for b in book["bids"][:5])
    ask_vol = sum(float(a[1]) for a in book["asks"][:5])
    
    if ask_vol == 0:
        return 999
    return bid_vol / ask_vol

def is_bb_flat(ma, upper, lower, threshold=0.0020):  # 稍微放宽带宽判断
    width = (upper - lower) / ma
    return width < threshold

def send_wechat(title, content):
    url = f"https://sctapi.ftqq.com/{SERVERCHAN_SENDKEY}.send"
    data = {"title": title, "desp": content}
    try:
        requests.post(url, data=data, timeout=8)
    except Exception as e:
        print(f"推送失败: {e}")

def get_winrate(period):
    s = stats[period]
    if s["total"] == 0:
        return "0.0%"
    return f"{s['win']/s['total']*100:.1f}% ({s['win']}/{s['total']})"

# ==================== 信号逻辑（已放宽） ====================
def check_signal():
    global last_signal_time
    
    now = time.time()
    if now - last_signal_time < SIGNAL_COOLDOWN:
        return None, "冷却中"
    
    try:
        df_1m = get_okx_klines("1m")
        df_3m = get_okx_klines("3m")
        df_5m = get_okx_klines("5m")
        df_15m = get_okx_klines("15m")
    except Exception as e:
        return None, f"获取K线失败: {e}"
    
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
        strength = get_okx_orderbook_strength()
    except Exception as e:
        return None, f"盘口获取失败: {e}"
    
    # 量能（稍微放宽）
    vol_1m = df_1m["volume"].iloc[-3:].mean()
    vol_prev = df_1m["volume"].iloc[-6:-3].mean()
    vol_up = vol_1m > vol_prev * 1.05
    
    # ========== 做多条件（已放宽） ==========
    near_15_lower = price <= low15 * 1.0025
    near_15_mid   = abs(price - ma15) / ma15 < 0.0018
    not_near_upper = price < up15 * 0.9980
    
    near_3_mid = abs(price - ma3) / ma3 < 0.0012
    near_5_mid = abs(price - ma5) / ma5 < 0.0012
    # 放宽不追条件
    not_chase_long = True   # 暂时关闭严格不追，提高触发率
    
    long_ok = (
        (near_15_lower or near_15_mid) and
        not_near_upper and
        flat_score >= 1 and                 # 从2降到1
        strength > 1.15 and                 # 从1.25降到1.15
        vol_up
    )
    
    # ========== 做空条件（已放宽） ==========
    near_15_upper = price >= up15 * 0.9975
    near_15_mid_s = abs(price - ma15) / ma15 < 0.0018
    not_near_lower = price > low15 * 1.0020
    
    short_ok = (
        (near_15_upper or near_15_mid_s) and
        not_near_lower and
        flat_score >= 1 and
        strength < 0.87 and                 # 从0.80放宽到0.87
        vol_up
    )
    
    if long_ok:
        last_signal_time = now
        reason = (
            f"【做多信号】\n"
            f"价格: {price:.1f}\n"
            f"15分中轨: {ma15:.1f} 下轨: {low15:.1f}\n"
            f"欧易买/卖比: {strength:.2f}\n"
            f"布林带走平数: {flat_score}"
        )
        return "LONG", reason
    
    if short_ok:
        last_signal_time = now
        reason = (
            f"【做空信号】\n"
            f"价格: {price:.1f}\n"
            f"15分中轨: {ma15:.1f} 上轨: {up15:.1f}\n"
            f"欧易买/卖比: {strength:.2f}\n"
            f"布林带走平数: {flat_score}"
        )
        return "SHORT", reason
    
    return None, "无信号"

# ==================== 双周期模拟 ====================
def simulate_both(direction="LONG"):
    entry_price = float(get_okx_klines("1m", 5)["close"].iloc[-1])
    start_time = datetime.now(timezone.utc)
    direction_cn = "做多" if direction == "LONG" else "做空"
    
    send_wechat(
        f"【{direction_cn}信号】开始模拟",
        f"方向: {direction_cn}\n"
        f"入场价: {entry_price:.1f}\n"
        f"将同时模拟 3分钟 + 5分钟 事件\n"
        f"当前3分钟胜率: {get_winrate('3m')}\n"
        f"当前5分钟胜率: {get_winrate('5m')}"
    )
    
    # ----- 等待3分钟 -----
    time.sleep(3 * 60 + 5)
    price_3m = float(get_okx_klines("1m", 5)["close"].iloc[-1])
    
    if direction == "LONG":
        win_3m = price_3m > entry_price
    else:
        win_3m = price_3m < entry_price
    
    stats["3m"]["total"] += 1
    if win_3m:
        stats["3m"]["win"] += 1
    
    result_3m = "赢" if win_3m else "输"
    
    # ----- 再等2分钟到5分钟 -----
    time.sleep(2 * 60 + 3)
    price_5m = float(get_okx_klines("1m", 5)["close"].iloc[-1])
    
    if direction == "LONG":
        win_5m = price_5m > entry_price
    else:
        win_5m = price_5m < entry_price
    
    stats["5m"]["total"] += 1
    if win_5m:
        stats["5m"]["win"] += 1
    
    result_5m = "赢" if win_5m else "输"
    
    # 推送最终结果
    send_wechat(
        f"模拟结果（{direction_cn}）",
        f"入场价: {entry_price:.1f}\n\n"
        f"【3分钟】\n"
        f"出场价: {price_3m:.1f} → {result_3m}\n"
        f"3分钟胜率: {get_winrate('3m')}\n\n"
        f"【5分钟】\n"
        f"出场价: {price_5m:.1f} → {result_5m}\n"
        f"5分钟胜率: {get_winrate('5m')}"
    )
    
    print(f"[{start_time}] {direction_cn} | 3m:{result_3m} 5m:{result_5m} | "
          f"3m胜率:{get_winrate('3m')} 5m胜率:{get_winrate('5m')}")

# ==================== 主循环 ====================
def main():
    print("多空信号系统启动（信号已放宽 + 双周期模拟）...")
    send_wechat(
        "系统上线",
        "事件合约多空信号系统已启动\n"
        "信号条件已放宽\n"
        "信号后同时模拟3分钟 + 5分钟事件并统计胜率"
    )
    
    while True:
        try:
            direction, reason = check_signal()
            if direction:
                print(f"[{datetime.now()}] 触发信号\n{reason}")
                simulate_both(direction=direction)
            else:
                print(f"[{datetime.now()}] {reason}")
            
            time.sleep(12)
            
        except Exception as e:
            print(f"主循环错误: {e}")
            time.sleep(20)

if __name__ == "__main__":
    main()
