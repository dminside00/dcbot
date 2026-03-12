"""
dcwatch_bot.py
==============
디시인사이드 마이너 갤러리 감시 봇 (GitHub Actions용 원샷 모드)

감시 규칙:
  Rule 1. 워뇨띠 (uid: rlawo200)  → 말머리 무관, 모든 글
  Rule 2. 야신난다 (uid: lovegod93) → 차트/토론 말머리 글만
"""

import os
import json
import logging
import requests
from pathlib import Path
from bs4 import BeautifulSoup

# ─────────────────────────────────────────
# 설정 (GitHub Secrets → 환경변수로 주입)
# ─────────────────────────────────────────
TELEGRAM_TOKEN  = os.environ.get("TELEGRAM_TOKEN",  "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
GALLERY_ID      = "chartanalysis"
SEEN_IDS_FILE   = "seen_ids.json"

# 감시 규칙 정의
RULES = [
    {
        "uid":    "rlawo200",
        "nick":   "워뇨띠",
        "prefix": None,          # None = 말머리 무관
    },
    {
        "uid":    "lovegod93",
        "nick":   "야신난다",
        "prefix": "차트/토론",    # 이 말머리만 감시
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

LIST_URL = "https://gall.dcinside.com/mgallery/board/lists/"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


# ── 유틸 ──────────────────────────────────

def load_seen_ids() -> set:
    p = Path(SEEN_IDS_FILE)
    if p.exists():
        with open(p, encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_seen_ids(seen: set):
    # 최대 2000개만 유지 (repo 용량 관리)
    ids = sorted(seen, key=lambda x: int(x) if x.isdigit() else 0)
    if len(ids) > 2000:
        ids = ids[-2000:]
    with open(SEEN_IDS_FILE, "w", encoding="utf-8") as f:
        json.dump(ids, f, ensure_ascii=False)


# ── 파싱 ──────────────────────────────────

def fetch_posts(page: int = 1) -> list[dict]:
    """갤러리 목록 파싱. 식별코드(data-uid)로 작성자 식별."""
    params = {"id": GALLERY_ID, "page": page}
    try:
        r = requests.get(LIST_URL, params=params, headers=HEADERS, timeout=15)
        r.raise_for_status()
    except requests.RequestException as e:
        logger.warning(f"요청 실패 (page={page}): {e}")
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    posts = []

    for row in soup.select("tr.ub-content"):
        # ── 게시글 번호 (숫자가 아니면 공지·배너 등 → 스킵)
        num_td = row.select_one("td.gall_num")
        if not num_td:
            continue
        post_id = num_td.get_text(strip=True)
        if not post_id.isdigit():
            continue

        # ── 말머리
        # 마이너 갤러리 말머리: td.gall_subject 또는 td.gall_tit 내 em.subject
        prefix = ""
        subject_td = row.select_one("td.gall_subject")
        if subject_td:
            prefix = subject_td.get_text(strip=True)
        else:
            em_tag = row.select_one("td.gall_tit em.subject")
            if em_tag:
                prefix = em_tag.get_text(strip=True)

        # ── 제목 & 링크
        title_a = row.select_one("td.gall_tit a:not(.reply_numbox)")
        title = title_a.get_text(strip=True) if title_a else ""
        href  = title_a.get("href", "") if title_a else ""
        link  = f"https://gall.dcinside.com{href}" if href.startswith("/") else href

        # ── 작성자 (data-uid = 식별코드, data-nick = 닉네임)
        writer_td = row.select_one("td.gall_writer")
        uid  = writer_td.get("data-uid", "").strip()  if writer_td else ""
        nick = writer_td.get("data-nick", "").strip() if writer_td else ""

        posts.append({
            "id":     post_id,
            "prefix": prefix,
            "title":  title,
            "link":   link,
            "uid":    uid,
            "nick":   nick,
        })

    return posts


# ── 텔레그램 ───────────────────────────────

def send_alert(post: dict, rule: dict):
    label = f"[{post['prefix']}] " if post["prefix"] else ""
    text = (
        f"🔔 *새 글 알림*\n\n"
        f"👤 *{post['nick']}*  `{post['uid']}`\n"
        f"🏷 말머리: {post['prefix'] or '없음'}\n"
        f"📝 {label}{post['title']}\n\n"
        f"[→ 글 보러가기]({post['link']})"
    )
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False,
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        r.raise_for_status()
        logger.info(f"✅ 알림 전송: [{post['uid']}] {post['id']} - {post['title']}")
    except requests.RequestException as e:
        logger.error(f"❌ 텔레그램 전송 실패: {e}")


# ── 메인 ──────────────────────────────────

def main():
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logger.error("TELEGRAM_TOKEN 또는 TELEGRAM_CHAT_ID 환경변수가 없습니다.")
        return

    seen = load_seen_ids()
    logger.info(f"기존 seen_ids: {len(seen)}개")

    # 최신 2페이지 수집 (누락 방지)
    all_posts = []
    for page in (1, 2):
        all_posts.extend(fetch_posts(page))

    new_alerts = 0
    for post in all_posts:
        if post["id"] in seen:
            continue

        seen.add(post["id"])

        for rule in RULES:
            # uid 일치 확인
            if post["uid"] != rule["uid"]:
                continue

            # 말머리 조건 확인 (None이면 무조건 통과)
            if rule["prefix"] is not None and post["prefix"] != rule["prefix"]:
                continue

            send_alert(post, rule)
            new_alerts += 1
            break  # 한 게시글에 중복 알림 방지

    save_seen_ids(seen)
    logger.info(f"완료. 새 알림: {new_alerts}건 / 전체 수집: {len(all_posts)}건")


if __name__ == "__main__":
    main()
