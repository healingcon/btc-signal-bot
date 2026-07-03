"""
BTC Signal Bot — 텔레그램 명령어 응답기
사용자가 봇에 메시지 보내면 5분 이내 자동 응답

지원 명령어:
  /price  또는  가격   → 현재가 + 진입시 손절/익절
  /score  또는  점수   → AI 점수 상세
  /status 또는  상태   → 전체 현황
  /help   또는  도움   → 명령어 목록
"""

import os, json, time, requests
from datetime import datetime, timezone, timedelta

TG_TOKEN   = os.environ['TG_TOKEN']
TG_CHAT_ID = os.environ['TG_CHAT_ID']
SYMBOL     = os.environ.get('SYMBOL', 'BTCUSDT')
SCORE_THRESHOLD = int(os.environ.get('SCORE_THRESHOLD', '70'))
KST = timezone(timedelta(hours=9))

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
}

# ── Binance 데이터 ────────────────────────────────────────
KLINE_URLS = [
    'https://api.binance.com/api/v3/klines',
    'https://api1.binance.com/api/v3/klines',
    'https://data-api.binance.vision/api/v3/klines',
]

def fetch_klines(interval='15m', limit=200):
    params = {'symbol': SYMBOL, 'interval': interval, 'limit': limit}
    for url in KLINE_URLS:
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=12)
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, list) and len(data) > 10:
                    return [{'time':k[0],'open':float(k[1]),'high':float(k[2]),
                             'low':float(k[3]),'close':float(k[4]),'volume':float(k[5])}
                            for k in data]
        except: pass
    return []

def fetch_price():
    urls = ['https://api.binance.com/api/v3/ticker/24hr',
            'https://data-api.binance.vision/api/v3/ticker/24hr']
    for url in urls:
        try:
            r = requests.get(url, params={'symbol': SYMBOL}, headers=HEADERS, timeout=8)
            if r.status_code == 200:
                d = r.json()
                return float(d['lastPrice']), float(d['priceChangePercent']), \
                       float(d['highPrice']), float(d['lowPrice']), float(d['volume'])
        except: pass
    return 0, 0, 0, 0, 0

# ── 지표 계산 ─────────────────────────────────────────────
def ema(closes, p):
    if len(closes) < p: return None
    k = 2/(p+1); v = sum(closes[:p])/p
    for c in closes[p:]: v = c*k + v*(1-k)
    return v

def rsi(closes, p=14):
    if len(closes) < p+1: return 50
    g=l=0
    for i in range(1,p+1):
        d = closes[i]-closes[i-1]
        if d>0: g+=d
        else: l-=d
    ag,al = g/p, l/p
    for i in range(p+1, len(closes)):
        d = closes[i]-closes[i-1]
        ag = (ag*(p-1)+max(d,0))/p; al = (al*(p-1)+max(-d,0))/p
    return 100 if al==0 else 100-100/(1+ag/al)

def calc_atr(candles, p=14):
    trs = [max(c['high']-c['low'], abs(c['high']-candles[i-1]['close']),
               abs(c['low']-candles[i-1]['close']))
           for i,c in enumerate(candles) if i>0]
    if len(trs)<p: return 0
    v = sum(trs[:p])/p
    for t in trs[p:]: v = (v*(p-1)+t)/p
    return v

def calc_ichimoku(candles):
    if len(candles)<52: return {}
    n=len(candles); hs=[c['high'] for c in candles]; ls=[c['low'] for c in candles]
    tk=(max(hs[n-9:n])+min(ls[n-9:n]))/2; kj=(max(hs[n-26:n])+min(ls[n-26:n]))/2
    return {'tenkan':tk,'kijun':kj,'senkouA':(tk+kj)/2,
            'senkouB':(max(hs[n-52:n])+min(ls[n-52:n]))/2}

def calc_score(c15m, c1h, c4h, price):
    cl15=[c['close'] for c in c15m]; cl1h=[c['close'] for c in c1h]; cl4h=[c['close'] for c in c4h]
    e20=ema(cl15,20); e60=ema(cl15,60); r=rsi(cl15); a=calc_atr(c15m)
    ichi=calc_ichimoku(c15m)
    vols=[c['volume'] for c in c15m]; va=sum(vols[-20:])/20; vc=vols[-1]
    e20_1h=ema(cl1h,20); e60_1h=ema(cl1h,60)
    e20_4h=ema(cl4h,20); e60_4h=ema(cl4h,60)
    ct=max(ichi.get('senkouA',0),ichi.get('senkouB',0))
    items=[
        ('구름대 상단 돌파',15,bool(ct) and price>ct),
        ('EMA20 > EMA60',  15,bool(e20 and e60) and e20>e60),
        ('거래량 증가',     15,vc>va),
        ('RSI > 55',       10,r>55),
        ('1시간봉 상승',    15,bool(e20_1h and e60_1h) and e20_1h>e60_1h),
        ('4시간봉 상승',    15,bool(e20_4h and e60_4h) and e20_4h>e60_4h),
        ('ATR 정상',         5,bool(a) and a<price*0.03),
        ('경제이벤트 없음', 10,True),
    ]
    return sum(p for _,p,ok in items if ok), items, a, e20, e60, r

def signal_label(s):
    if s>=90: return '🚀 강력 롱'
    if s>=70: return '📈 롱 우세'
    if s>=50: return '⏸ 관망'
    if s>=30: return '📉 숏 우세'
    return '💥 강력 숏'

# ── 텔레그램 API ──────────────────────────────────────────
def tg_get(method, params=None):
    r = requests.get(f'https://api.telegram.org/bot{TG_TOKEN}/{method}',
                     params=params, timeout=10)
    return r.json()

def tg_send(chat_id, msg):
    requests.post(f'https://api.telegram.org/bot{TG_TOKEN}/sendMessage',
                  json={'chat_id': chat_id, 'text': msg, 'parse_mode': 'HTML'},
                  timeout=10)
    print(f"[TG] 응답 전송: {msg[:40]}...")

# ── 업데이트 오프셋 저장 ─────────────────────────────────
OFFSET_FILE = 'tg_offset.json'

def load_offset():
    try:
        with open(OFFSET_FILE) as f:
            return json.load(f).get('offset', 0)
    except:
        return 0

def save_offset(offset):
    with open(OFFSET_FILE, 'w') as f:
        json.dump({'offset': offset}, f)

# ── 응답 메시지 생성 ─────────────────────────────────────
def make_price_msg(price, pct, high, low, vol, score, items, atr_val, e20, e60, rsi_val):
    coin      = SYMBOL.replace('USDT','')
    direction = 'LONG' if score >= 50 else 'SHORT'
    is_long   = direction == 'LONG'
    sl_dist   = atr_val * 1.5
    now       = datetime.now(KST).strftime('%H:%M:%S')

    entry = price
    sl    = entry - sl_dist  if is_long else entry + sl_dist
    tp1   = entry + sl_dist  if is_long else entry - sl_dist
    tp2   = entry + sl_dist*2 if is_long else entry - sl_dist*2
    tp3   = entry + sl_dist*3 if is_long else entry - sl_dist*3

    sl_pct = abs(sl_dist / price * 100)
    bar    = '█' * int(score/10) + '░' * (10 - int(score/10))

    return f"""💰 <b>{coin} 실시간 현황</b>  <i>{now} KST</i>

현재가:  <code>{price:,.0f} USDT</code>  ({pct:+.2f}%)
24H 고:  <code>{high:,.0f}</code>  |  저:  <code>{low:,.0f}</code>

🤖 AI 점수:  <b>{score}/100</b>  {signal_label(score)}
[{bar}]
EMA20: {e20:,.0f}  |  EMA60: {e60:,.0f}  |  RSI: {rsi_val:.1f}

━━━ 지금 진입 시 레벨 ━━━
{'📈 LONG 기준' if is_long else '📉 SHORT 기준'}
💰 진입가:  <code>{entry:,.0f} USDT</code>
🛑 손절가:  <code>{sl:,.0f} USDT</code>  (-{sl_pct:.2f}%)
🎯 익절1:   <code>{tp1:,.0f} USDT</code>  (+{sl_pct:.2f}%)
🎯 익절2:   <code>{tp2:,.0f} USDT</code>  (+{sl_pct*2:.2f}%)
🎯 익절3:   <code>{tp3:,.0f} USDT</code>  (+{sl_pct*3:.2f}%)
📐 ATR: {atr_val:.0f}  |  손절폭: {sl_dist:.0f} USDT

⚠️ 참고용입니다. 투자 결정은 본인 판단으로."""

def make_score_msg(score, items, price, pct):
    coin = SYMBOL.replace('USDT','')
    now  = datetime.now(KST).strftime('%H:%M:%S')
    bd   = '\n'.join(f"  {'✅' if ok else '⬜'} {n}  +{p}점" for n,p,ok in items)
    earned = sum(p for _,p,ok in items if ok)

    return f"""🤖 <b>{coin} AI 점수 상세</b>  <i>{now} KST</i>

현재가: <code>{price:,.0f} USDT</code>  ({pct:+.2f}%)
총점: <b>{score}/100</b>  {signal_label(score)}

{bd}

{'🔔 진입 신호 조건: ' + str(SCORE_THRESHOLD) + '점 이상 (롱) / ' + str(100-SCORE_THRESHOLD) + '점 이하 (숏)'}
현재 {score}점 → {'⚡ 신호 발생!' if score>=SCORE_THRESHOLD or score<=(100-SCORE_THRESHOLD) else '아직 관망 구간'}"""

def make_help_msg():
    coin = SYMBOL.replace('USDT','')
    return f"""🤖 <b>BTC Signal Bot 명령어</b>

/price  또는  <b>가격</b>
→ 현재 {coin} 가격 + 진입시 손절/익절

/score  또는  <b>점수</b>
→ AI 점수 상세 분석

/status 또는  <b>상태</b>
→ 전체 현황 리포트

/help   또는  <b>도움</b>
→ 이 메시지

📌 신호가 없어도 언제든 물어보세요!
응답 시간: 최대 5분 이내"""

# ── 메인: 새 메시지 체크 & 응답 ──────────────────────────
def main():
    print(f"[{datetime.now(KST).strftime('%H:%M:%S')}] 명령어 봇 실행")

    offset = load_offset()
    resp   = tg_get('getUpdates', {'offset': offset, 'timeout': 0, 'limit': 10})

    if not resp.get('ok'):
        print(f"[ERR] getUpdates 실패: {resp}")
        return

    updates = resp.get('result', [])
    if not updates:
        print("새 메시지 없음")
        return

    # 데이터는 명령어 있을 때만 한 번 가져옴
    data_loaded = False
    price = pct = high = low = vol = score = atr_val = e20 = e60 = rsi_val = 0
    items = []

    def load_data():
        nonlocal data_loaded, price, pct, high, low, vol
        nonlocal score, atr_val, e20, e60, rsi_val, items
        if data_loaded: return
        print("📡 Binance 데이터 로딩...")
        c15m = fetch_klines('15m', 200)
        c1h  = fetch_klines('1h',  100)
        c4h  = fetch_klines('4h',  100)
        price, pct, high, low, vol = fetch_price()
        if not price and c15m:
            price = c15m[-1]['close']
        if c15m:
            score, items, atr_val, e20_, e60_, rsi_val = calc_score(c15m, c1h, c4h, price)
            e20 = e20_ or 0; e60 = e60_ or 0
        data_loaded = True
        print(f"💰 {price:,.0f} USDT | 점수: {score}/100")

    new_offset = offset
    for update in updates:
        uid  = update.get('update_id', 0)
        new_offset = max(new_offset, uid + 1)

        msg     = update.get('message') or update.get('edited_message', {})
        chat_id = msg.get('chat', {}).get('id')
        text    = (msg.get('text') or '').strip().lower()

        # 허용된 Chat ID만 응답
        if str(chat_id) != str(TG_CHAT_ID):
            print(f"[SKIP] 다른 채팅: {chat_id}")
            continue

        print(f"[MSG] '{text}'")

        if text in ['/price', '가격', '/price@btcsignalbot']:
            load_data()
            tg_send(chat_id, make_price_msg(
                price, pct, high, low, vol, score, items, atr_val, e20, e60, rsi_val))

        elif text in ['/score', '점수', '/score@btcsignalbot']:
            load_data()
            tg_send(chat_id, make_score_msg(score, items, price, pct))

        elif text in ['/status', '상태', '/status@btcsignalbot']:
            load_data()
            # price + score 합친 전체 현황
            tg_send(chat_id, make_price_msg(
                price, pct, high, low, vol, score, items, atr_val, e20, e60, rsi_val))
            time.sleep(0.5)
            tg_send(chat_id, make_score_msg(score, items, price, pct))

        elif text in ['/help', '도움', '/start', '/help@btcsignalbot']:
            tg_send(chat_id, make_help_msg())

        else:
            tg_send(chat_id, f"❓ 명령어를 입력해주세요.\n\n/help 로 사용법 확인")

    save_offset(new_offset)
    print(f"✅ 완료 (오프셋: {new_offset})")

if __name__ == '__main__':
    main()
