"""유저 피처 → 클러스터 배정 (클러스터적용 노트북과 동일 로직 — 수정 시 양쪽 동기화 필수)."""

import numpy as np
import pandas as pd

from core.push import artifacts


def kp_preprocess(df: pd.DataFrame, prep: dict) -> pd.DataFrame:
    d = df.copy()
    d["REC_FILL"] = d["RECENCY_DAYS"].fillna(400)
    for c in ("WATCH_CAT", "CLICK_CAT", "ORDER_CAT"):
        d[c] = d[c].fillna("없음")
        d[c] = d[c].where(d[c].isin(prep["top_cats"][c]) | (d[c] == "없음"), "기타")
    d["PRIMARY_HOUR_BUCKET"] = d["PRIMARY_HOUR_BUCKET"].fillna("9_no_log")
    for src, dst in (("REC_FILL", "REC_LOG_Z"), ("ORDER_COUNT_3M", "ORD_LOG_Z")):
        m, s = prep["z_stats"][dst]
        d[dst] = (np.log1p(d[src].astype(float)) - m) / s
    return d


def predict_clusters(features: pd.DataFrame, chunk: int = 100_000) -> np.ndarray:
    """피처 DataFrame → 클러스터 라벨 (대용량은 chunk 단위 — kmodes predict 메모리/속도 대비)."""
    art = artifacts.load()
    d = kp_preprocess(features, art["PREP"])
    X = d[art["NUM_COLS"] + art["CAT_COLS"]].to_numpy()
    if len(X) <= chunk:
        return art["KP"].predict(X, categorical=art["CAT_IDX"])
    parts = [
        art["KP"].predict(X[i : i + chunk], categorical=art["CAT_IDX"])
        for i in range(0, len(X), chunk)
    ]
    return np.concatenate(parts)
