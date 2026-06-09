"""백그라운드 job — threading + 인메모리 store (로컬 단일 유저 전제).

SNOWFLAKE_LOCK: core.datasources.snowflake_kp 의 전역 세션은 TEMPORARY TABLE 파이프라인을 공유하므로
job 끼리 직렬화한다. 산출물은 파일(cache/)이라 프로세스 재시작으로 job 기록이 사라져도 결과는 유지된다.
"""

import threading
import uuid
from typing import Callable

JOBS: dict[str, dict] = {}
SNOWFLAKE_LOCK = threading.Lock()
_START_LOCK = threading.Lock()  # running() 체크 ~ 등록 사이 TOCTOU 방지


def start_exclusive(kind: str, fn: Callable, **kwargs) -> str | None:
    """같은 kind 의 job 이 돌고 있으면 None (시작 안 함) — 체크와 등록을 원자적으로."""
    with _START_LOCK:
        if running(kind):
            return None
        return start_job(kind, fn, **kwargs)


def start_job(kind: str, fn: Callable, **kwargs) -> str:
    job_id = uuid.uuid4().hex[:12]
    JOBS[job_id] = {"job_id": job_id, "kind": kind, "status": "running", "stage": "대기 중 (이전 작업 직렬화)", "error": None}

    def _run() -> None:
        with SNOWFLAKE_LOCK:
            JOBS[job_id]["stage"] = "시작"
            try:
                fn(progress=lambda s: JOBS[job_id].update(stage=s), **kwargs)
                JOBS[job_id].update(status="done", stage="완료")
            except Exception as e:  # noqa: BLE001 — job 은 실패 사유를 그대로 노출
                JOBS[job_id].update(status="error", error=f"{type(e).__name__}: {e}")

    threading.Thread(target=_run, daemon=True).start()
    return job_id


def get_job(job_id: str) -> dict | None:
    return JOBS.get(job_id)


def running(kind: str) -> bool:
    return any(j["kind"] == kind and j["status"] == "running" for j in JOBS.values())
