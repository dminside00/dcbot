"""
bot.py
======
디시인사이드 차트분석 마이너 갤러리 감시 봇
GitHub Actions 원샷 방식으로 실행

감시 규칙:
  1. 워뇨띠  → 작성자 검색 결과의 모든 새 글 알림
  2. 야신난다 → 작성자 검색 결과 중 말머리가 '차트/' 인 글만 알림
"""

import os
import json
import logging
import requests
from pathlib import Path
from bs4 import BeautifulSoup

# ── 설정 (GitHub Secrets → 환경변수) ─────
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
SEEN_IDS_FILE    = "seen_ids.json"

TARGETS = [
    {
        "nick":          "워뇨띠",
        "search_url":    "https://gall.dcinside.com/mgallery/board/lists/?id=chartanalysis&s_type=search_name&s_keyword=%EC%9B%8C%EB%87%A8%EB%9D%A0",
        "prefix_filter": None,   # None = 모든 글 알림
    },
    {
        "nick":          "야신난다",
        "search_url":    "https://gall.dcinside.com/mgallery/board/lists/?id=chartanalysis&s_type=search_name&s_keyword=%EC%95%BC%EC%8B%A0%EB%82%9C%EB%8B%A4",
        "prefix_filter": "차트/",  # 말머리가 '차트/'로 시작하는 글만
    },
]
# ─────────────────────────────────────────

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://gall.dcinside.com/",
    "Accept-Language": "ko-KR,ko;q=0.9",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


# ── seen_ids 관리 ─────────────────────────

def load_seen() -> set:
    p = Path(SEEN_IDS_FILE)
    if p.exists():
        with open(p, encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_seen(seen: set):
    ids = sorted(seen, key=lambda x: int(x) if x.isdigit() else 0)
    if len(ids) > 2000:
        ids = ids[-2000:]
    with open(SEEN_IDS_FILE, "w", encoding="utf-8") as f:
        json.dump(ids, f, ensure_ascii=False)


# ── 페이지 파싱 ───────────────────────────

def fetch_posts(url: str) -> list[dict]:
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
    except requests.RequestException as e:
        log.warning(f"요청 실패: {e}")
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    posts = []

    for row in soup.select("tr.ub-content"):
        # 숫자 번호가 아니면 공지 등 → 스킵
        num_td = row.select_one("td.gall_num")
        if not num_td:
            continue
        post_id = num_td.get_text(strip=True)
        if not post_id.isdigit():
            continue

        # 말머리 (갤러리마다 위치 다를 수 있어 두 셀렉터 시도)
        prefix = ""
        for sel in ("td.gall_subject", "td.gall_tit em.subject"):
            tag = row.select_one(sel)
            if tag:
                prefix = tag.get_text(strip=True)
                break

        # 제목 & 링크
        title_a = row.select_one("td.gall_tit a:not(.reply_numbox)")
        title = title_a.get_text(strip=True) if title_a else ""
        href  = title_a.get("href", "")       if title_a else ""
        link  = f"https://gall.dcinside.com{href}" if href.startswith("/") else href

        posts.append({
            "id":     post_id,
            "prefix": prefix,
            "title":  title,
            "link":   link,
        })

    return posts


# ── 텔레그램 알림 ─────────────────────────

def send_alert(nick: str, post: dict):
    label = f"[{post['prefix']}] " if post["prefix"] else ""
    text = (
        f"🔔 *새 글 알림*\n\n"
        f"👤 *{nick}*\n"
        f"🏷 말머리: {post['prefix'] or '없음'}\n"
        f"📝 {label}{post['title']}\n\n"
        f"[→ 글 보러가기]({post['link']})"
    )
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={
                "chat_id":                  TELEGRAM_CHAT_ID,
                "text":                     text,
                "parse_mode":               "Markdown",
                "disable_web_page_preview": False,
            },
            timeout=10,
        )
        r.raise_for_status()
        log.info(f"✅ 알림 전송: {nick} | {post['id']} | {post['title']}")
    except requests.RequestException as e:
        log.error(f"❌ 텔레그램 전송 실패: {e}")


# ── 메인 ──────────────────────────────────

def main():
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log.error("TELEGRAM_TOKEN 또는 TELEGRAM_CHAT_ID 환경변수 없음")
        return

    seen = load_seen()
    log.info(f"seen_ids 로드: {len(seen)}개")

    total_alerts = 0

    for target in TARGETS:
        nick    = target["nick"]
        filter_ = target["prefix_filter"]
        posts   = fetch_posts(target["search_url"])
        log.info(f"[{nick}] 수집: {len(posts)}건")

        for post in posts:
            if post["id"] in seen:
                continue
            seen.add(post["id"])

            # 말머리 필터 (None이면 무조건 통과)
            if filter_ is not None and not post["prefix"].startswith(filter_):
                continue

            send_alert(nick, post)
            total_alerts += 1

    save_seen(seen)
    log.info(f"완료 | 알림: {total_alerts}건")


if __name__ == "__main__":
    main()
