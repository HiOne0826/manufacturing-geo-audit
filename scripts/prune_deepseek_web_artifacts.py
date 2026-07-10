from __future__ import annotations

import argparse
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description="统计或显式清理 DeepSeek 网页采样证据")
    parser.add_argument("--before", required=True, help="删除此日期之前的 task 目录，格式 YYYY-MM-DD")
    parser.add_argument("--execute", action="store_true", help="实际删除；默认只做 dry-run")
    parser.add_argument("--root", default=os.environ.get("DEEPSEEK_WEB_ARTIFACT_DIR", "data/deepseek-web-artifacts"))
    args = parser.parse_args()
    cutoff = datetime.strptime(args.before, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()
    artifact_root = Path(args.root).expanduser()
    if not artifact_root.is_absolute():
        artifact_root = ROOT / artifact_root
    candidates = []
    total_bytes = 0
    if artifact_root.exists():
        for batch_dir in artifact_root.iterdir():
            if not batch_dir.is_dir():
                continue
            for task_dir in batch_dir.iterdir():
                if not task_dir.is_dir() or task_dir.stat().st_mtime >= cutoff:
                    continue
                size = sum(path.stat().st_size for path in task_dir.rglob("*") if path.is_file())
                total_bytes += size
                candidates.append((task_dir, size))
    for path, size in candidates:
        print(f"{'DELETE' if args.execute else 'WOULD_DELETE'}\t{size}\t{path}")
        if args.execute:
            shutil.rmtree(path)
    print({"directories": len(candidates), "bytes": total_bytes, "executed": args.execute})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
