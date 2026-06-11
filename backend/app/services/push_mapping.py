"""푸시 방송 자동 매핑 — push_seq + 판매자이름으로 방송(content)을 발견해 커버리지 매핑에 추가.

수동 content_seq 탐색을 대체한다: 판매자가 push_at 전후([-2h, +3h))에 연 방송을 자동 발견하고,
오픈→유효시청→구매 펀넬을 미리보기로 함께 계산한다. 사용자가 확인한 뒤
push_content_id_map.json 에 {content_ids, category} 로 저장 → [집계 갱신] 시 '세그먼트별 전환·커버리지'에 반영.

user_name 은 자유 입력이라 작은따옴표만 이스케이프(문자열 리터럴 안전). push_seq·user_seq 는 int.
"""

import json
import os
from datetime import datetime

import pandas as pd

from app.services.push_funnel import compute_funnel
from core.datasources.snowflake_kp import run_query
from core.push.paths import CACHE_DIR

PUSH_CONTENT_ID_MAP_JSON = CACHE_DIR / "push_content_id_map.json"


def discover_content(push_seq: int, user_name: str) -> dict:
    """push_seq + 판매자이름 → 발송시각 전후의 방송 발견 + 발송수·펀넬 미리보기·경고.

    반환: {push_seq, push_at, title, n_sent, seller_name, seller_matches, contents:[...], funnel:{}|None, warnings:[...]}
    contents 비었거나 n_sent=0 이면 저장 전 경고(가드). 프론트는 경고 없을 때만 저장 버튼 활성화.
    """
    push_seq = int(push_seq)
    name = (user_name or "").strip()
    out: dict = {
        "push_seq": push_seq, "push_at": None, "title": None, "n_sent": 0,
        "seller_name": name, "seller_matches": 0,
        "contents": [], "funnel": None, "warnings": [],
    }
    if not name:
        out["warnings"].append("판매자이름을 입력하세요.")
        return out

    # 1) 푸시 존재 + 제목 (grip_db.push)
    meta = run_query(f"SELECT push_seq, push_at, title FROM grip_db.push WHERE push_seq = {push_seq}")
    meta.columns = meta.columns.str.lower()
    if not len(meta):
        out["warnings"].append(f"push_seq {push_seq} 가 존재하지 않아요.")
        return out
    r = meta.iloc[0]
    sched_at = pd.Timestamp(r["push_at"]).strftime("%Y-%m-%d %H:%M:%S") if pd.notna(r["push_at"]) else None
    out["title"] = str(r["title"]) if pd.notna(r["title"]) else None

    # 발송시각(MIN, 최종 집계 _meta_sql 과 동일 기준=send_status='Y') + 발송수(token_valid='Y')
    # — 미리보기 수치가 [집계 갱신] 최종 결과와 일치하도록 send_history 의 발송시각을 쓴다(예약시각 아님).
    snd = run_query(
        f"SELECT MIN(push_at) AS push_at, "
        f"COUNT(DISTINCT CASE WHEN token_valid='Y' THEN user_seq END) AS n "
        f"FROM data_anal.push_send_history WHERE send_status='Y' AND push_seq = {push_seq}"
    )
    snd.columns = snd.columns.str.lower()
    sent_at = (
        pd.Timestamp(snd.iloc[0]["push_at"]).strftime("%Y-%m-%d %H:%M:%S")
        if len(snd) and pd.notna(snd.iloc[0]["push_at"]) else None
    )
    out["n_sent"] = int(snd.iloc[0]["n"]) if len(snd) and pd.notna(snd.iloc[0]["n"]) else 0
    out["push_at"] = sent_at or sched_at  # 발송시각(최종 집계 동일) 우선, 없으면 예약시각
    if out["n_sent"] == 0:
        out["warnings"].append("이 push_seq 의 발송 기록(send_status=Y)이 없어요 — 발송된 푸시인지 확인하세요.")

    # 2) 판매자 매칭 (정확 일치 · 파라미터 바인딩으로 SQL 주입/리터럴 깨짐 차단)
    mem = run_query(
        "SELECT user_seq, user_id, user_name FROM grip_db_realtime.member WHERE user_name = %s",
        (name,),
    )
    mem.columns = mem.columns.str.lower()
    out["seller_matches"] = len(mem)
    if not len(mem):
        out["warnings"].append("판매자이름과 정확히 일치하는 회원이 없어요 — 띄어쓰기·표기(대소문자·이모지)를 확인하세요.")
        return out
    if len(mem) > 1:
        out["warnings"].append(f"판매자이름이 {len(mem)}명과 일치해요(동명이인 가능) — 발견된 방송이 맞는지 확인하세요.")

    # 3) 방송 발견 (발송시각 전후 [-2h, +3h), 실제 방영분만 view_count/duration > 0)
    if not out["push_at"]:
        out["warnings"].append("푸시 발송시각이 없어 방송을 찾을 수 없어요.")
        return out
    user_seqs = ", ".join(str(int(s)) for s in mem["user_seq"])
    c = run_query(f"""
        SELECT content_seq, content_id, title, created_at, view_count, duration
        FROM   grip_db_realtime.content
        WHERE  view_count > 0 AND duration > 0
          AND  created_at >= DATEADD(hour, -2, TIMESTAMP '{out["push_at"]}')
          AND  created_at <  DATEADD(hour, 3, TIMESTAMP '{out["push_at"]}')
          AND  user_seq IN ({user_seqs})
        ORDER BY created_at
    """)
    c.columns = c.columns.str.lower()
    out["contents"] = [
        {
            "content_seq": int(x.content_seq),
            "content_id": str(x.content_id),
            "title": str(x.title) if pd.notna(x.title) else None,
            "created_at": str(pd.Timestamp(x.created_at)) if pd.notna(x.created_at) else None,
            "view_count": int(x.view_count) if pd.notna(x.view_count) else 0,
        }
        for x in c.itertuples(index=False)
    ]
    if not out["contents"]:
        out["warnings"].append(
            "발송 전후(−2h~+3h)에 이 판매자의 방송을 찾지 못했어요 — 판매자이름/푸시를 확인하세요(사전예고·VOD 푸시는 수동 매핑 필요)."
        )
        return out

    # 4) 펀넬 미리보기 (오픈→유효시청→구매, push_at 직접 주입 → push_result 미적재 푸시도 동작, 저장 안 함)
    try:
        out["funnel"] = compute_funnel(
            push_seq, [c["content_seq"] for c in out["contents"]], push_at=out["push_at"]
        )
    except Exception as e:  # 미리보기 실패해도 발견 자체는 반환
        out["warnings"].append(f"전환 미리보기 계산 실패: {e}")
        out["funnel"] = None
    return out


def read_content_id_map() -> dict:
    if not PUSH_CONTENT_ID_MAP_JSON.exists():
        return {}
    with open(PUSH_CONTENT_ID_MAP_JSON, encoding="utf-8") as f:
        return json.load(f)


def upsert_content_id_map(
    push_seq: int, content_ids: list[str], category: str,
    seller_name: str | None = None, title: str | None = None,
) -> None:
    """push_content_id_map.json 에 {content_ids, category, seller, title, updated_at} upsert (원자적 교체)."""
    m = read_content_id_map()
    m[str(int(push_seq))] = {
        "content_ids": sorted({str(c) for c in content_ids}),
        "category": category,
        "seller": seller_name,
        "title": title,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = str(PUSH_CONTENT_ID_MAP_JSON) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False, indent=2)
    os.replace(tmp, PUSH_CONTENT_ID_MAP_JSON)
