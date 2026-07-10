from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.deepseek_web import DeepSeekWebBrowser, DeepSeekWebError
from src.runtime_env import load_dotenv_file


def main() -> int:
    load_dotenv_file(ROOT / ".env")
    parser = argparse.ArgumentParser(description="直接执行少量 DeepSeek 官网真实采样")
    parser.add_argument("--question", action="append", required=True)
    parser.add_argument("--batch-id", default=f"live-web-{datetime.now().strftime('%Y%m%d-%H%M%S')}")
    args = parser.parse_args()
    browser = DeepSeekWebBrowser()
    results = []
    try:
        for index, question in enumerate(args.question, start=1):
            task_id = f"live-{index:03d}"
            try:
                result = browser.sample(batch_id=args.batch_id, task_id=task_id, question=question)
                results.append({"question": question, "task_id": task_id, "status": "success", **result})
            except DeepSeekWebError as exc:
                results.append(
                    {
                        "question": question,
                        "task_id": task_id,
                        "status": "failed",
                        "error_code": exc.code,
                        "error_message": str(exc),
                        "artifact_dir": getattr(exc, "artifact_dir", ""),
                    }
                )
                break
    finally:
        browser.close()
    print(json.dumps({"batch_id": args.batch_id, "results": results}, ensure_ascii=False, indent=2))
    return 0 if len(results) == len(args.question) and all(item["status"] == "success" for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
