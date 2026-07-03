"""
BTC AI Signal Bot
- 15분마다 GitHub Actions로 실행
- Binance API로 지표 계산
- 조건 충족 시 텔레그램 전송
"""

import os, json, math, time, requests
from datetime import datetime, timezone, timedelta

# ── 환경변수 (GitHub Secrets) ──────────────────────────────
TG_TOKEN        = os.environ['TG_TOKEN']
TG_CHAT_ID      = os.environ['TG_CHAT_ID']
SYMBOL          = os.environ.get('SYMBOL', 'BTCUSDT')
SCORE_THRESHOLD = int(os.environ.get('SCORE_THRESHOLD', '70'))

KST = timezone(timedelta(hours=9))

# ── Binance API — 다중 엔드포인트 + User-Agent ─────────────
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept': 'application/json',
}

KLINE_URLS = [
    'https://api.binance.com/api/v3/klines',
    'https://api1.binance.com/api/v3/klines',
    'https://api2.binance.com/api/v3/klines',
    'https://api3.binance.com/api/v3/klines',
    'https://data-api.binance.vision/api/v3/klines',   # 공개 미러
]

TICKER_URLS = [
    'https://api.binance.com/api/v3/ticker/24hr',
    'https://api1.binance.com/api/v3/ticker/24hr',
    'https://data-api.binance.vision/api/v3/ticker/24hr',
]

def fetch_klines(interval='15m', limit=200):
    params = {'symbol': SYMBOL, 'interval': interval, 'limit': limit}
    for url in KLINE_URLS:
        for attempt in range(2):   # URL당 2회 재시도
            try:
                r = requests.get(url, params=params,
                                 headers=HEADERS, timeout=15)
                if r.status_code == 200:
                    data = r.json()
                    if isinstance(data, list) and len(data) > 10:
                        print(f"[OK] {url} ({interval})")
                        return [{
                            'time':   k[0],
                            'open':   float(k[1]),
                            'high':   float(k[2]),
                            'low':    float(k[3]),
                            'close':  float(k[4]),
                            'volume': float(k[5]),
                        } for k in data]
                print(f"[WARN] {url} 상태코드: {r.status_code}")
            except Exception as e:
                print(f"[WARN] {url} 시도{attempt+1} 실패: {e}")
            time.sleep(1)
    # 모두 실패 시 — 예외 대신 텔레그램으로 알림 후 종료
    send_tg(f"⚠️ BTC Signal Bot\nBinance API 연결 실패 ({interval})\n잠시 후 자동 재시도됩니다.")
    raise SystemExit("Binance API 전체 실패 — 다음 실행에서 재시도")

def fetch_price():
    params = {'symbol': SYMBOL}
    for url in TICKER_URLS:
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=10)
            if r.status_code == 200:
                d = r.json()
                return float(d['lastPrice']), float(d['priceChangePercent'])
        except:
            pass
    return 0, 0

# ── 지표 계산 ──────────────────────────────────────────────
def ema(closes, period):
    if len(closes) < period:
        return None
    k = 2 / (period + 1)
    v = sum(closes[:period]) / period
    for c in closes[period:]:
        v = c * k + v * (1 - k)
    return v

def rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50
    g = l = 0
    for i in range(1, period + 1):
        d = closes[i] - closes[i-1]
        if d > 0: g += d
        else:     l -= d
    ag, al = g/period, l/period
    for i in range(period+1, len(closes)):
        d = closes[i] - closes[i-1]
        ag = (ag*(period-1) + max(d,0)) / period
        al = (al*(period-1) + max(-d,0)) / period
    return 100 if al == 0 else 100 - 100/(1 + ag/al)

def atr(candles, period=14):
    trs = []
    for i in range(1, len(candles)):
        hl = candles[i]['high'] - candles[i]['low']
        hc = abs(candles[i]['high'] - candles[i-1]['close'])
        lc = abs(candles[i]['low']  - candles[i-1]['close'])
        trs.append(max(hl, hc, lc))
    if len(trs) < period:
        return 0
    v = sum(trs[:period]) / period
    for t in trs[period:]:
        v = (v*(period-1) + t) / period
    return v

def ichimoku(candles):
    if len(candles) < 52:
        return {}
    n = len(candles)
    hs = [c['high'] for c in candles]
    ls = [c['low']  for c in candles]
    tenkan  = (max(hs[n-9:n])  + min(ls[n-9:n]))  / 2
    kijun   = (max(hs[n-26:n]) + min(ls[n-26:n])) / 2
    senkouA = (tenkan + kijun) / 2
    senkouB = (max(hs[n-52:n]) + min(ls[n-52:n])) / 2
    return {'tenkan': tenkan, 'kijun': kijun,
            'senkouA': senkouA, 'senkouB': senkouB}

# ── AI 점수 계산 ───────────────────────────────────────────
def calc_score(c15m, c1h, c4h, price):
    cl15 = [c['close'] for c in c15m]
    cl1h = [c['close'] for c in c1h]
    cl4h = [c['close'] for c in c4h]

    e20 = ema(cl15, 20); e60 = ema(cl15, 60)
    r   = rsi(cl15)
    a   = atr(c15m)
    ichi = ichimoku(c15m)

    vols    = [c['volume'] for c in c15m]
    vol_avg = sum(vols[-20:]) / 20
    vol_cur = vols[-1]

    e20_1h = ema(cl1h, 20); e60_1h = ema(cl1h, 60)
    e20_4h = ema(cl4h, 20); e60_4h = ema(cl4h, 60)
    cloud_top = max(ichi.get('senkouA', 0), ichi.get('senkouB', 0))

    items = [
        ('구름대 상단 돌파', 15, bool(cloud_top) and price > cloud_top),
        ('EMA20 > EMA60',   15, bool(e20 and e60) and e20 > e60),
        ('거래량 증가',      15, vol_cur > vol_avg),
        ('RSI > 55',        10, r > 55),
        ('1시간봉 상승',     15, bool(e20_1h and e60_1h) and e20_1h > e60_1h),
        ('4시간봉 상승',     15, bool(e20_4h and e60_4h) and e20_4h > e60_4h),
        ('ATR 정상',          5, bool(a) and a < price * 0.03),
        ('경제이벤트 없음',  10, True),
    ]

    score = sum(pts for _, pts, active in items if active)
    return score, items, a, ichi

def signal_label(score):
    if score >= 90: return '🚀 강력 롱'
    if score >= 70: return '📈 롱 우세'
    if score >= 50: return '⏸ 관망'
    if score >= 30: return '📉 숏 우세'
    return '💥 강력 숏'

# ── 손절/익절 계산 ─────────────────────────────────────────
def calc_levels(price, atr_val, direction):
    sl_dist = atr_val * 1.5
    sign = 1 if direction == 'LONG' else -1
    return {
        'entry': price,
        'sl':  price - sign * sl_dist,
        'tp1': price + sign * sl_dist,
        'tp2': price + sign * sl_dist * 2,
        'tp3': price + sign * sl_dist * 3,
    }

# ── 텔레그램 전송 ──────────────────────────────────────────
def send_tg(msg):
    try:
        r = requests.post(
            f'https://api.telegram.org/bot{TG_TOKEN}/sendMessage',
            json={'chat_id': TG_CHAT_ID, 'text': msg, 'parse_mode': 'HTML'},
            timeout=10
        )
        ok = r.json().get('ok', False)
        print("✅ 텔레그램 전송 성공" if ok else f"❌ 전송 실패: {r.json()}")
        return ok
    except Exception as e:
        print(f"❌ 텔레그램 오류: {e}")
        return False

def format_entry_msg(score, direction, price, pct, lv, atr_val, items):
    now   = datetime.now(KST).strftime('%Y.%m.%d %p %I:%M')
    emoji = '🟢' if direction == 'LONG' else '🔴'
    coin  = SYMBOL.replace('USDT', '')
    bd    = '\n'.join(f"  {'✅' if ok else '⬜'} {n} ({p}점)"
                      for n, p, ok in items)
    sl_pct = abs((lv['sl'] - price) / price * 100)

    return f"""{emoji} <b>{coin} {direction} 진입 신호</b>

{signal_label(score)}  |  AI 점수: <b>{score}/100</b>
━━━━━━━━━━━━━━━━━━━━
💰 현재가:  <code>{price:,.0f} USDT</code>  ({pct:+.2f}%)
🛑 손절가:  <code>{lv['sl']:,.0f} USDT</code>  ({sl_pct:.2f}% 위험)
🎯 익절1 (1:1):  <code>{lv['tp1']:,.0f} USDT</code>
🎯 익절2 (1:2):  <code>{lv['tp2']:,.0f} USDT</code>
🎯 익절3 (1:3):  <code>{lv['tp3']:,.0f} USDT</code>
📐 ATR: {atr_val:.0f}
━━━━━━━━━━━━━━━━━━━━
<b>점수 상세</b>
{bd}
━━━━━━━━━━━━━━━━━━━━
📅 {now} KST

⚠️ 참고용 신호입니다. 투자 결정은 본인 판단으로."""

def format_tp_msg(tp_label, tp_price, price, entry, direction):
    now  = datetime.now(KST).strftime('%Y.%m.%d %p %I:%M')
    coin = SYMBOL.replace('USDT', '')
    pnl  = ((price-entry)/entry*100 if direction=='LONG'
            else (entry-price)/entry*100)
    return f"""🎯 <b>{coin} {tp_label} 도달!</b>

✅ 목표가:  <code>{tp_price:,.0f} USDT</code>
💹 현재가:  <code>{price:,.0f} USDT</code>
📈 수익률:  <b>+{pnl:.2f}%</b>

💡 일부 또는 전체 익절을 고려하세요.
📅 {now} KST"""

def format_sl_msg(sl_price, price, entry, direction):
    now  = datetime.now(KST).strftime('%Y.%m.%d %p %I:%M')
    coin = SYMBOL.replace('USDT', '')
    pnl  = ((price-entry)/entry*100 if direction=='LONG'
            else (entry-price)/entry*100)
    return f"""🛑 <b>{coin} 손절 구간 도달!</b>

⚠️ 손절가:  <code>{sl_price:,.0f} USDT</code>
💹 현재가:  <code>{price:,.0f} USDT</code>
📉 손익률:  <b>{pnl:.2f}%</b>

🔴 손절을 고려하세요. 리스크 관리 최우선!
📅 {now} KST"""

# ── 상태 파일 (이전 점수/레벨 저장) ──────────────────────
STATE_FILE = 'signal_state.json'

def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except:
        return {
            'last_score':    50,
            'last_direction': None,
            'last_levels':   None,
            'tp_sent':       {'tp1': False, 'tp2': False, 'tp3': False},
            'sl_sent':       False,
        }

def save_state(s):
    with open(STATE_FILE, 'w') as f:
        json.dump(s, f, indent=2)

# ── 메인 ──────────────────────────────────────────────────
def main():
    print(f"[{datetime.now(KST).strftime('%Y-%m-%d %H:%M')} KST] BTC Signal Bot 실행")

    print("📡 Binance 데이터 수신 중...")
    c15m = fetch_klines('15m', 200)
    c1h  = fetch_klines('1h',  100)
    c4h  = fetch_klines('4h',  100)
    price, pct = fetch_price()
    if not price:
        price = c15m[-1]['close']

    print(f"💰 현재가: ${price:,.0f} ({pct:+.2f}%)")

    score, items, atr_val, ichi = calc_score(c15m, c1h, c4h, price)
    direction = 'LONG' if score >= 50 else 'SHORT'
    anti      = 100 - SCORE_THRESHOLD

    print(f"📊 AI 점수: {score}/100 | {direction} | {signal_label(score)}")

    state = load_state()
    prev  = state.get('last_score', 50)

    # ── 진입 신호 ──────────────────────────────────────────
    if ((score >= SCORE_THRESHOLD and prev < SCORE_THRESHOLD) or
            (score <= anti and prev > anti)):
        print(f"🔔 신호! {prev} → {score} | {direction}")
        lv = calc_levels(price, atr_val, direction)
        state['last_levels'] = lv
        state['last_direction'] = direction
        state['tp_sent'] = {'tp1': False, 'tp2': False, 'tp3': False}
        state['sl_sent'] = False
        send_tg(format_entry_msg(score, direction, price, pct, lv, atr_val, items))
    else:
        print(f"⏸ 신호 없음 (현재:{score} 이전:{prev} 임계:{SCORE_THRESHOLD})")

    # ── TP / SL 체크 ───────────────────────────────────────
    lv = state.get('last_levels')
    ld = state.get('last_direction')
    if lv and ld and (score >= SCORE_THRESHOLD or score <= anti):
        is_long = ld == 'LONG'
        for key, tp_price, tp_label in [
            ('tp1', lv['tp1'], '익절1 (1:1)'),
            ('tp2', lv['tp2'], '익절2 (1:2)'),
            ('tp3', lv['tp3'], '익절3 (1:3)'),
        ]:
            if not state['tp_sent'].get(key):
                if (is_long and price >= tp_price) or (not is_long and price <= tp_price):
                    state['tp_sent'][key] = True
                    print(f"🎯 {tp_label} 도달!")
                    send_tg(format_tp_msg(tp_label, tp_price, price, lv['entry'], ld))

        if not state.get('sl_sent'):
            sl_hit = (is_long and price <= lv['sl']) or (not is_long and price >= lv['sl'])
            if sl_hit:
                state['sl_sent'] = True
                print("🛑 손절 도달!")
                send_tg(format_sl_msg(lv['sl'], price, lv['entry'], ld))

    state['last_score'] = score
    save_state(state)
    print("✅ 완료")

if __name__ == '__main__':
    main()
