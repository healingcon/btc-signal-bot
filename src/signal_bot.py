"""
BTC AI Signal Bot
- 15분마다 GitHub Actions로 실행
- Binance API로 지표 계산
- 조건 충족 시 텔레그램 전송
"""

import os, json, math, requests
from datetime import datetime, timezone, timedelta

# ── 환경변수 (GitHub Secrets) ──────────────────────────────
TG_TOKEN   = os.environ['TG_TOKEN']
TG_CHAT_ID = os.environ['TG_CHAT_ID']
SYMBOL     = os.environ.get('SYMBOL', 'BTCUSDT')
SCORE_THRESHOLD = int(os.environ.get('SCORE_THRESHOLD', '70'))

KST = timezone(timedelta(hours=9))

# ── Binance API ────────────────────────────────────────────
BINANCE_URLS = [
    'https://api.binance.com/api/v3/klines',
    'https://api1.binance.com/api/v3/klines',
    'https://api2.binance.com/api/v3/klines',
]

def fetch_klines(interval='15m', limit=200):
    for url in BINANCE_URLS:
        try:
            r = requests.get(url, params={
                'symbol': SYMBOL, 'interval': interval, 'limit': limit
            }, timeout=10)
            data = r.json()
            if isinstance(data, list) and len(data) > 10:
                return [{
                    'time':   k[0],
                    'open':   float(k[1]),
                    'high':   float(k[2]),
                    'low':    float(k[3]),
                    'close':  float(k[4]),
                    'volume': float(k[5]),
                } for k in data]
        except Exception as e:
            print(f"[WARN] {url} 실패: {e}")
    raise Exception("모든 Binance 엔드포인트 실패")

def fetch_price():
    for base in ['https://api.binance.com', 'https://api1.binance.com']:
        try:
            r = requests.get(f"{base}/api/v3/ticker/24hr",
                             params={'symbol': SYMBOL}, timeout=8)
            d = r.json()
            return float(d['lastPrice']), float(d['priceChangePercent'])
        except:
            pass
    return 0, 0

# ── 지표 계산 ──────────────────────────────────────────────
def ema(closes, period):
    if len(closes) < period:
        return None
    k   = 2 / (period + 1)
    val = sum(closes[:period]) / period
    for c in closes[period:]:
        val = c * k + val * (1 - k)
    return val

def rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50
    gains = losses = 0
    for i in range(1, period + 1):
        d = closes[i] - closes[i-1]
        if d > 0: gains += d
        else:     losses -= d
    avg_g, avg_l = gains/period, losses/period
    for i in range(period+1, len(closes)):
        d = closes[i] - closes[i-1]
        avg_g = (avg_g*(period-1) + max(d,0)) / period
        avg_l = (avg_l*(period-1) + max(-d,0)) / period
    return 100 if avg_l == 0 else 100 - 100/(1 + avg_g/avg_l)

def atr(candles, period=14):
    trs = []
    for i in range(1, len(candles)):
        hl = candles[i]['high'] - candles[i]['low']
        hc = abs(candles[i]['high'] - candles[i-1]['close'])
        lc = abs(candles[i]['low']  - candles[i-1]['close'])
        trs.append(max(hl, hc, lc))
    if len(trs) < period:
        return 0
    val = sum(trs[:period]) / period
    for t in trs[period:]:
        val = (val*(period-1) + t) / period
    return val

def ichimoku(candles):
    if len(candles) < 52:
        return {}
    highs = [c['high'] for c in candles]
    lows  = [c['low']  for c in candles]
    n = len(candles)
    tenkan = (max(highs[n-9:n])  + min(lows[n-9:n]))  / 2
    kijun  = (max(highs[n-26:n]) + min(lows[n-26:n])) / 2
    senkouA = (tenkan + kijun) / 2
    senkouB = (max(highs[n-52:n]) + min(lows[n-52:n])) / 2
    return {'tenkan': tenkan, 'kijun': kijun, 'senkouA': senkouA, 'senkouB': senkouB}

# ── AI 점수 계산 ───────────────────────────────────────────
def calc_score(candles_15m, candles_1h, candles_4h, price):
    closes_15m = [c['close'] for c in candles_15m]
    closes_1h  = [c['close'] for c in candles_1h]
    closes_4h  = [c['close'] for c in candles_4h]

    e20 = ema(closes_15m, 20)
    e60 = ema(closes_15m, 60)
    r   = rsi(closes_15m)
    a   = atr(candles_15m)
    ichi = ichimoku(candles_15m)

    vols    = [c['volume'] for c in candles_15m]
    avg_vol = sum(vols[-20:]) / 20
    cur_vol = vols[-1]

    e20_1h = ema(closes_1h, 20); e60_1h = ema(closes_1h, 60)
    e20_4h = ema(closes_4h, 20); e60_4h = ema(closes_4h, 60)

    cloud_top = max(ichi.get('senkouA',0), ichi.get('senkouB',0))

    items = [
        ('구름대 상단 돌파', 15, bool(cloud_top) and price > cloud_top),
        ('EMA20 > EMA60',   15, bool(e20 and e60) and e20 > e60),
        ('거래량 증가',      15, cur_vol > avg_vol),
        ('RSI > 55',        10, r > 55),
        ('1시간봉 상승',     15, bool(e20_1h and e60_1h) and e20_1h > e60_1h),
        ('4시간봉 상승',     15, bool(e20_4h and e60_4h) and e20_4h > e60_4h),
        ('ATR 정상',          5, bool(a) and a < price * 0.03),
        ('경제이벤트 없음',  10, True),  # 기본값 (별도 체크 가능)
    ]

    score = sum(pts for _, pts, active in items if active)
    return score, items, e20, e60, r, a, ichi

def signal_label(score):
    if score >= 90: return '🚀 강력 롱'
    if score >= 70: return '📈 롱 우세'
    if score >= 50: return '⏸ 관망'
    if score >= 30: return '📉 숏 우세'
    return '💥 강력 숏'

# ── 손절/익절 계산 ─────────────────────────────────────────
def calc_levels(price, atr_val, direction, rr=2):
    sl_dist = atr_val * 1.5
    if direction == 'LONG':
        sl  = price - sl_dist
        tp1 = price + sl_dist
        tp2 = price + sl_dist * 2
        tp3 = price + sl_dist * 3
    else:
        sl  = price + sl_dist
        tp1 = price - sl_dist
        tp2 = price - sl_dist * 2
        tp3 = price - sl_dist * 3
    return sl, tp1, tp2, tp3

# ── 텔레그램 전송 ──────────────────────────────────────────
def send_tg(msg):
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    r = requests.post(url, json={
        'chat_id': TG_CHAT_ID,
        'text':    msg,
        'parse_mode': 'HTML'
    }, timeout=10)
    result = r.json()
    if result.get('ok'):
        print("✅ 텔레그램 전송 성공")
    else:
        print(f"❌ 전송 실패: {result}")
    return result.get('ok', False)

def format_signal_msg(score, direction, price, pct, sl, tp1, tp2, tp3, atr_val, items):
    now_kst = datetime.now(KST).strftime('%Y.%m.%d %p %I:%M')
    emoji   = '🟢' if direction == 'LONG' else '🔴'
    label   = signal_label(score)
    coin    = SYMBOL.replace('USDT', '')

    breakdown = '\n'.join(
        f"  {'✅' if active else '⬜'} {name} ({pts}점)"
        for name, pts, active in items
    )

    return f"""{emoji} <b>{coin} {direction} 진입 신호</b>

{label}  |  AI 점수: <b>{score}/100</b>
━━━━━━━━━━━━━━━━━━━━
💰 현재가:  <code>{price:,.0f} USDT</code>  ({pct:+.2f}%)
🛑 손절가:  <code>{sl:,.0f} USDT</code>  ({abs((sl-price)/price*100):.2f}% 위험)
🎯 익절1 (1:1):  <code>{tp1:,.0f} USDT</code>
🎯 익절2 (1:2):  <code>{tp2:,.0f} USDT</code>
🎯 익절3 (1:3):  <code>{tp3:,.0f} USDT</code>
📐 ATR: {atr_val:.0f}  |  RR: 1:2
━━━━━━━━━━━━━━━━━━━━
<b>점수 상세</b>
{breakdown}
━━━━━━━━━━━━━━━━━━━━
📅 {now_kst} KST

⚠️ 참고용 신호입니다. 투자 결정은 본인 판단으로."""

# ── 상태 파일 (이전 점수 저장) ────────────────────────────
STATE_FILE = '/tmp/btc_signal_state.json'

def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except:
        return {'last_score': 50, 'last_direction': None, 'last_signal_time': 0}

def save_state(state):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f)

# ── 메인 ──────────────────────────────────────────────────
def main():
    print(f"[{datetime.now(KST).strftime('%Y-%m-%d %H:%M')} KST] BTC Signal Bot 실행")

    # 데이터 가져오기
    print("📡 Binance 데이터 수신 중...")
    candles_15m = fetch_klines('15m', 200)
    candles_1h  = fetch_klines('1h',  100)
    candles_4h  = fetch_klines('4h',  100)
    price, pct  = fetch_price()

    if not price:
        price = candles_15m[-1]['close']

    print(f"💰 현재가: ${price:,.0f} ({pct:+.2f}%)")

    # 지표 & 점수 계산
    score, items, e20, e60, r, a, ichi = calc_score(
        candles_15m, candles_1h, candles_4h, price
    )
    direction = 'LONG' if score >= 50 else 'SHORT'
    print(f"📊 AI 점수: {score}/100  |  방향: {direction}  |  {signal_label(score)}")

    # 이전 상태 불러오기
    state = load_state()
    prev_score = state.get('last_score', 50)
    anti_thresh = 100 - SCORE_THRESHOLD

    # 신호 발생 여부 체크
    signal_triggered = (
        (score >= SCORE_THRESHOLD and prev_score < SCORE_THRESHOLD) or
        (score <= anti_thresh and prev_score > anti_thresh)
    )

    if signal_triggered:
        print(f"🔔 신호 발생! 점수 {prev_score} → {score}")
        sl, tp1, tp2, tp3 = calc_levels(price, a, direction)
        msg = format_signal_msg(score, direction, price, pct, sl, tp1, tp2, tp3, a, items)
        send_tg(msg)
        state['last_direction']    = direction
        state['last_signal_time']  = datetime.now().timestamp()
    else:
        print(f"⏸ 신호 없음 (점수: {score}, 이전: {prev_score}, 임계값: {SCORE_THRESHOLD})")

    # 상태 저장
    state['last_score'] = score
    save_state(state)
    print("✅ 완료")

if __name__ == '__main__':
    main()
