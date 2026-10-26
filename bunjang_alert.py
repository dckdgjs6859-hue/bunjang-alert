"""
번개장터 새 상품 알림 프로그램 (폰 알림: 텔레그램)
- 지정한 브랜드(검색어)를 최신순으로 주기적으로 검색해서
  새 상품이 올라오면 텔레그램으로 사진과 함께 알려줍니다.

설치:
    pip install requests

실행:
    python bunjang_alert.py
"""

import os
import signal
import json
import time
import random
from datetime import datetime
from pathlib import Path

import requests

# ================== 설정 ==================
BRANDS = [
    "셔터", "비헤비어", "디그레", "스투시", "아워레가시",   # 감시할 브랜드/검색어
]

INCLUDE_KEYWORDS = []    # 제목에 이 단어가 있어야 알림. 예: ["후드", "자켓"] (비우면 전부)
EXCLUDE_KEYWORDS = ["구매", "삽니다", "구해요"]   # 이 단어가 있으면 제외
MIN_PRICE = None         # 최소 가격(원). 예: 10000
MAX_PRICE = None         # 최대 가격(원). 예: 150000

CHECK_INTERVAL_MIN = 10  # 몇 분마다 확인할지 (내 컴퓨터에서 돌릴 때만 사용)

TELEGRAM_BOT_TOKEN = ""  # @BotFather 에서 받은 토큰
TELEGRAM_CHAT_ID = ""    # 내 chat id
# ==========================================

# 클라우드(GitHub Actions)에서는 비밀값을 환경변수로 받음
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") or TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID") or TELEGRAM_CHAT_ID
RUN_ONCE = os.environ.get("RUN_ONCE") == "1"   # 1이면 한 번만 확인하고 종료

API_URL = "https://api.bunjang.co.kr/api/search/v8/mw/product/specs/keyword"
PRODUCT_URL = "https://m.bunjang.co.kr/products/{pid}"
SEEN_FILE = Path(__file__).with_name("bunjang_seen.json")
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"),
    "Accept": "application/json",
    "Origin": "https://m.bunjang.co.kr",
    "Referer": "https://m.bunjang.co.kr/",
}


def log(msg):
    print(f"[{datetime.now():%m-%d %H:%M:%S}] {msg}", flush=True)


def load_seen():
    if SEEN_FILE.exists():
        return json.loads(SEEN_FILE.read_text(encoding="utf-8"))
    return {}


def save_seen(seen):
    SEEN_FILE.write_text(json.dumps(seen, ensure_ascii=False, indent=2), encoding="utf-8")


def to_int(v):
    try:
        return int(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def search(query):
    params = {"q": query, "sort": "latest"}
    r = requests.get(API_URL, params=params, headers=HEADERS, timeout=(5, 10))
    r.raise_for_status()
    data = r.json()

    # 응답 안에서 상품 목록 블록(productList.grid.main)을 찾기
    blocks = (data.get("data") or {}).get("searchSpec", {}).get("uiBlockList") or []
    products = []
    for b in blocks:
        if b.get("blockType") == "productList.grid.main":
            products = (b.get("searchResponse") or {}).get("data") or []
            break

    items = []
    for p in products:
        pid = str(p.get("pid") or "")
        if not pid or p.get("ad"):          # 광고 상품 제외
            continue
        if p.get("status") not in (None, "SELLING"):   # 예약중/판매완료 제외
            continue
        img = p.get("productImage") or ""
        items.append({
            "id": pid,
            "title": p.get("name") or "(제목 없음)",
            "price": to_int(p.get("price")),
            "location": "",
            "img": img.replace("{res}", "400") if img else "",
            "url": PRODUCT_URL.format(pid=pid),
        })
    return items


def passes_filter(item):
    t = item["title"].lower()
    if INCLUDE_KEYWORDS and not any(k.lower() in t for k in INCLUDE_KEYWORDS):
        return False
    if any(k.lower() in t for k in EXCLUDE_KEYWORDS):
        return False
    price = item["price"]
    if price is not None:
        if MIN_PRICE is not None and price < MIN_PRICE:
            return False
        if MAX_PRICE is not None and price > MAX_PRICE:
            return False
    return True


def send_telegram(text, img=None):
    if not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
        log("  (텔레그램 설정이 비어 있어 콘솔에만 출력)")
        return
    base = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
    try:
        if img:
            r = requests.post(f"{base}/sendPhoto",
                              data={"chat_id": TELEGRAM_CHAT_ID, "photo": img, "caption": text},
                              timeout=15)
            if r.ok:
                return
        requests.post(f"{base}/sendMessage",
                      data={"chat_id": TELEGRAM_CHAT_ID, "text": text},
                      timeout=15)
    except Exception as e:
        log(f"  텔레그램 전송 실패: {e}")


def notify(brand, item):
    price = f"{item['price']:,}원" if item["price"] is not None else "가격 정보 없음"
    loc = f"\n📍 {item['location']}" if item["location"] else ""
    text = f"⚡ [{brand}] 새 상품\n{item['title']}\n💰 {price}{loc}\n{item['url']}"
    log("  알림: " + text.replace("\n", " | "))
    send_telegram(text, item["img"])


def _give_up(signum, frame):
    raise TimeoutError("번개장터 응답이 너무 느림 (해외 서버 접속 차단 가능성)")


def main():
    if RUN_ONCE and hasattr(signal, "SIGALRM"):
        signal.signal(signal.SIGALRM, _give_up)
        signal.alarm(150)   # 클라우드 1회 실행은 최대 2분 30초
    seen = load_seen()
    if RUN_ONCE:
        log(f"번개장터 1회 확인: {', '.join(BRANDS)}")
        if not seen:   # 클라우드에서 맨 처음 실행할 때만 시작 메시지
            send_telegram(f"✅ 번개장터 알림 시작(클라우드): {', '.join(BRANDS)}")
    else:
        log(f"번개장터 감시 시작: {', '.join(BRANDS)} / {CHECK_INTERVAL_MIN}분 간격")
        send_telegram(f"✅ 번개장터 알림 시작: {', '.join(BRANDS)}")

    while True:
        errors = 0
        for brand in BRANDS:
            try:
                items = search(brand)
            except Exception as e:
                log(f"  '{brand}' 검색 오류: {e}")
                errors += 1
                continue

            first_run = brand not in seen
            known = set(seen.get(brand, []))
            new_items = [it for it in items if it["id"] not in known]

            if first_run:
                log(f"  '{brand}': 첫 실행 - 현재 {len(items)}개 저장 (알림 X)")
            else:
                log(f"  '{brand}': {len(items)}개 확인, 새 상품 {len(new_items)}개")
                for it in reversed(new_items):      # 오래된 것부터 순서대로 알림
                    if passes_filter(it):
                        notify(brand, it)
                        time.sleep(1)

            seen[brand] = ([it["id"] for it in items] + [i for i in seen.get(brand, []) if i not in {x["id"] for x in items}])[:1000]
            save_seen(seen)
            time.sleep(random.uniform(2, 4))

        if RUN_ONCE:
            if errors == len(BRANDS):
                raise SystemExit("모든 브랜드 검색이 실패했습니다 (번개장터 접속 차단 가능성)")
            return
        time.sleep(CHECK_INTERVAL_MIN * 60 + random.uniform(0, 20))


if __name__ == "__main__":
    main()
