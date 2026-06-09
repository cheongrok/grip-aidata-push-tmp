"""Snowflake key-pair 연결 (클러스터링/클러스터적용 노트북과 동일 인증).

두 종류의 실행 함수를 제공한다:
- run_snowflake / run_ddl : 모듈 전역 **단일 세션** — TEMPORARY TABLE 파이프라인용.
  스레드 안전하지 않으므로 백그라운드 job 에서 Lock 으로 직렬화해 사용할 것.
- run_query : 호출마다 새 연결 — 대시보드성 단발 조회용(스레드 안전, temp table 불가).

prod 원칙: SELECT + TEMPORARY TABLE 만 사용한다.
"""

import os
from pathlib import Path

import pandas as pd
import snowflake.connector
from cryptography.hazmat.primitives import serialization
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

_conn = None


def _private_key() -> bytes:
    path = os.environ.get("SNOWFLAKE_PRIVATE_KEY_PATH", str(Path.home() / ".snowflake" / "snowflake.p8"))
    with open(path, "rb") as f:
        k = serialization.load_pem_private_key(f.read(), password=None)
    return k.private_bytes(
        serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )


def _connect():
    return snowflake.connector.connect(
        user=os.environ.get("SNOWFLAKE_USER", "ROK805"),
        private_key=_private_key(),
        account=os.environ.get("SNOWFLAKE_ACCOUNT", "gl46460.ap-northeast-2.aws").replace(
            ".snowflakecomputing.com", ""
        ),
        warehouse=os.environ.get("SNOWFLAKE_WAREHOUSE", "XS_BATCH"),
        database=os.environ.get("SNOWFLAKE_DATABASE", "PROD_GRIP_DW"),
        schema=os.environ.get("SNOWFLAKE_SCHEMA", "DEFAULT"),
    )


def get_conn():
    """세션 전역 연결 (temp table 공유). 끊겼으면 재연결."""
    global _conn
    if _conn is None or _conn.is_closed():
        _conn = _connect()
    return _conn


def _fetch_df(cur) -> pd.DataFrame:
    try:
        return cur.fetch_pandas_all()  # arrow 경로 (대용량에서 수십 배 빠름)
    except Exception:
        cols = [d[0] for d in cur.description]
        return pd.DataFrame(cur.fetchall(), columns=cols)


def run_snowflake(sql: str) -> pd.DataFrame:
    """전역 세션에서 조회 → DataFrame. (job 파이프라인 전용 — Lock 하에서만)"""
    with get_conn().cursor() as cur:
        cur.execute(sql)
        return _fetch_df(cur)


def run_ddl(sql: str) -> None:
    """전역 세션에서 실행 (TEMPORARY TABLE 생성 등)."""
    with get_conn().cursor() as cur:
        cur.execute(sql)


def run_query(sql: str, params: tuple | dict | None = None) -> pd.DataFrame:
    """호출마다 새 연결로 조회 — API 단발 조회용 (스레드 안전, temp table 사용 불가).

    params 를 주면 %s 바인딩으로 실행한다. 자유입력 값(예: 판매자이름)은 반드시 바인딩으로 전달해
    SQL 주입/리터럴 깨짐(백슬래시 등)을 막을 것 — f-string 인라인 금지. params=None 이면 기존과 동일.
    """
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return _fetch_df(cur)
    finally:
        conn.close()
