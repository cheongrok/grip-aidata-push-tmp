"""클러스터링 노트북이 저장한 운영 아티팩트 lazy 로드.

- kp_artifacts.pkl       : KPrototypes 모델·전처리 파라미터·클러스터 설명 (kmodes 패키지 필수)
- cluster_push_stats.pkl : 5월 실측 클러스터 × 푸시카테고리 × 발송시간대 (n, opens)
- MAY_ORDER              : 발송 우선순위 = 5월 전체 오픈율(opens/n) 내림차순 (고정 — 새 푸시 성적으로 재정렬 금지)
"""

import pickle
from functools import lru_cache

import pandas as pd
from kmodes.kprototypes import KPrototypes  # noqa: F401 — pkl 역직렬화에 필요

from core.push.paths import ARTIFACTS_DIR


@lru_cache(maxsize=1)
def load() -> dict:
    with open(ARTIFACTS_DIR / "kp_artifacts.pkl", "rb") as f:
        art = pickle.load(f)
    stats = pd.read_pickle(ARTIFACTS_DIR / "cluster_push_stats.pkl")
    base = stats.groupby("CLUSTER")[["n", "opens"]].sum()
    may_order = (base["opens"] / base["n"]).sort_values(ascending=False).index.tolist()
    may_order = [int(c) for c in may_order]
    return {
        "KP": art["kp_model"],
        "PREP": art["prep"],
        "NUM_COLS": art["num_cols"],
        "CAT_COLS": art["cat_cols"],
        "CAT_IDX": art["cat_idx"],
        "K": int(art["k_final"]),
        "DESC": art["desc"],
        "FIT_META": art["fit_meta"],
        "STATS": stats,
        "BASE": base,
        "MAY_ORDER": may_order,
        # 표시용 1-based 번호 = 발송 우선순위(MAY_ORDER) 순서. 모델 원본 라벨(0~9)은
        # kmodes 가 임의로 매긴 값이라 직관성이 없어, 우선순위 순 1~10 으로 화면에 보여준다.
        # 미배정(-1)은 RANK 에 없음 → .get() 으로 None 처리.
        "RANK": {c: i + 1 for i, c in enumerate(may_order)},
    }


def categories() -> list[str]:
    return sorted(load()["STATS"]["PUSH_CAT"].unique().tolist())


def hour_buckets() -> list[str]:
    return sorted(load()["STATS"]["PUSH_HOUR_BUCKET"].unique().tolist())
