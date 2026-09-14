"""Low-cost, potentially billable Ark smoke test through the local HTTP contract.

The default path calls GLM vision and Seedream; ``--include-video`` also calls
Seedance. Stable idempotency keys avoid intentional duplicate submissions.
"""

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import requests


def api_json(response: requests.Response) -> dict | list:
    try:
        body = response.json()
    except ValueError as exc:
        raise RuntimeError(f"HTTP {response.status_code} returned non-JSON") from exc
    if not response.ok:
        raise RuntimeError(f"HTTP {response.status_code}: {body.get('detail', body)}")
    return body


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=Path)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--include-video", action="store_true")
    parser.add_argument("--timeout-minutes", type=int, default=25)
    args = parser.parse_args()

    image_path = args.image.resolve()
    if not image_path.is_file():
        raise FileNotFoundError(image_path)
    digest = hashlib.sha256(image_path.read_bytes()).hexdigest()
    base_url = args.base_url.rstrip("/")

    status = api_json(requests.get(f"{base_url}/api/system/status", timeout=15))
    if not status["ark"]["configured"]:
        raise RuntimeError("Local service does not have an Ark key configured")

    with image_path.open("rb") as handle:
        uploaded = api_json(requests.post(
            f"{base_url}/api/assets/images",
            files={"files": (image_path.name, handle, "application/octet-stream")},
            timeout=60,
        ))
    asset = uploaded[0]

    product = api_json(requests.post(
        f"{base_url}/api/products/analyze-vision",
        json={
            "asset_ids": [asset["asset_id"]],
            "preferred_scene": "真实办公室日常桌面",
            "idempotency_key": f"smoke:{digest}:vision:v1",
        },
        timeout=180,
    ))
    compiled = api_json(requests.post(
        f"{base_url}/api/prompts/compile",
        json={
            "product_id": product["product_id"],
            "version": "1.0",
            "provider": "volcengine",
            "model": status["ark"]["video_model"],
        },
        timeout=30,
    ))
    shot = next(item for item in compiled["shots"] if item["shot_id"] == "S01")
    idempotency_root = f"smoke:{digest}:S01:V1.0"
    frame = api_json(requests.post(
        f"{base_url}/api/images/first-frame",
        json={
            "product_id": product["product_id"],
            "shot_id": "S01",
            "prompt_version": "1.0",
            "prompt": shot["prompt"],
            "asset_ids": [asset["asset_id"]],
            "model": status["ark"]["image_model_primary"],
            "size": "1440x2560",
            "idempotency_key": f"{idempotency_root}:frame",
        },
        timeout=240,
    ))

    summary = {
        "asset_id": asset["asset_id"],
        "product_id": product["product_id"],
        "product_name": product["product_name"],
        "analysis_model": product["analysis_model"],
        "confidence": product["information_confidence"],
        "image_task_id": frame["image_task_id"],
        "image_model": frame["model"],
        "image_status": frame["status"],
        "image_url": frame["image_url"],
        "eleven_layers_present": all(f"【第 {index} 层" in shot["prompt"] for index in range(1, 12)),
    }
    if frame["status"] != "COMPLETED":
        summary["image_error"] = frame.get("error_message")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 2
    if not args.include_video:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    task = api_json(requests.post(
        f"{base_url}/api/video/generate",
        json={
            "product_id": product["product_id"],
            "shot_id": "S01",
            "provider": "volcengine",
            "model": status["ark"]["video_model"],
            "prompt_version": "1.0",
            "prompt": shot["prompt"],
            "negative_prompt": shot["negative_prompt"],
            "image_url": frame["image_url"],
            "duration": 5,
            "aspect_ratio": "9:16",
            "product_name": product["product_name"],
            "idempotency_key": f"{idempotency_root}:video:{frame['image_task_id']}",
        },
        timeout=30,
    ))
    deadline = time.monotonic() + args.timeout_minutes * 60
    last_status = ""
    while time.monotonic() < deadline:
        task = api_json(requests.get(
            f"{base_url}/api/video/tasks/{task['internal_task_id']}", timeout=30
        ))
        if task["status"] != last_status:
            print(f"Seedance task {task['internal_task_id']}: {task['status']}", flush=True)
            last_status = task["status"]
        if task["status"] not in {"CREATED", "SUBMITTED", "PROCESSING", "COMPLETED"}:
            break
        time.sleep(5)
    summary.update({
        "video_task_id": task["internal_task_id"],
        "provider_task_id": task["provider_task_id"],
        "video_model": task["model"],
        "video_status": task["status"],
        "video_url": task.get("video_url"),
        "local_video_path": task.get("local_video_path"),
        "video_error": task.get("error_message"),
    })
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if task["status"] == "QA_PENDING" else 3


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
