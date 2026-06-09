"""레포 절대경로 상수 — backend(cwd=backend/)와 노트북(cwd=루트) 어디서든 동일하게 동작."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS_DIR = REPO_ROOT / "artifacts"
CACHE_DIR = REPO_ROOT / "cache"
ALLOCATIONS_DIR = CACHE_DIR / "allocations"
LATEST_CLUSTER_CSV = CACHE_DIR / "latest_cluster.csv"
LATEST_CLUSTER_META = CACHE_DIR / "latest_cluster_meta.json"
