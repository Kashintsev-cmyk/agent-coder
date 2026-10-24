#!/usr/bin/env python3
"""Verify-бенчмарк: haiku-5.5 vs qwen3.7-flash на gold na_shtile."""

import asyncio
import json
import random
import sys
from pathlib import Path

from agent_coder.agent.session import Session
from agent_coder.agent.verify import Mode, run_verify
from agent_coder.llm.client import LLMClient

GOLD_PATH = Path("benchmark/gold_verify_na_shtile.json")
OUT_DIR = Path("benchmark/verify_results")
NA_SHTILE = "/home/botuser/bots/na_shtile"
MODELS = ["anthropic/claude-haiku-5.5", "qwen/qwen3.7-flash"]
REPEATS = 2
SHUFFLE_SEED = 42


def make_session(texts):
    sess = Session.new(
        workspace=NA_SHTILE,
        model="deepseek/deepseek-v4-flash",
        mode=Mode.AUDIT,
    )
    sess.work_state = {
        "user_requests": [],
        "findings": list(texts),
        "done": [],
        "todo": [],
        "open_questions": [],
    }
    return sess


async def run_one(client, model, texts, tag):
    sess = make_session(texts)
    try:
        await run_verify(client, sess, model=model)
    except Exception as e:
        return {
            "model": model,
            "tag": tag,
            "error": f"{type(e).__name__}: {e}",
            "results": [],
        }
    results = []
    for v in sess.verify_results:
        results.append(dict(v) if isinstance(v, dict) else vars(v))
    return {"model": model, "tag": tag, "results": results}


async def main():
    if not GOLD_PATH.exists():
        sys.exit(f"Нет {GOLD_PATH}")
    gold = json.loads(GOLD_PATH.read_text())
    items = [g for g in gold if g.get("manual_label") in ("confirmed", "rejected")]
    if len(items) < 15:
        sys.exit(f"gold incomplete: {len(items)}/{len(gold)} labeled")
    texts = [g["finding_text"] for g in items]
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    client = LLMClient()
    await client.start()
    manifest = {
        "gold_size": len(items),
        "models": MODELS,
        "repeats": REPEATS,
        "files": [],
    }

    for model in MODELS:
        for rep in range(REPEATS):
            print(f"→ {model} rep {rep + 1}/{REPEATS}")
            payload = await run_one(client, model, texts, f"rep{rep}")
            if payload.get("error"):
                print(f"  FAIL: {payload['error']}")
            out = OUT_DIR / f"{model.replace('/', '_')}__rep{rep}.json"
            out.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
            manifest["files"].append(str(out))
            print(f"  saved {out} ({len(payload.get('results', []))} results)")

    shuffled = list(texts)
    random.Random(SHUFFLE_SEED).shuffle(shuffled)
    for model in MODELS:
        print(f"→ {model} bias-probe (shuffled)")
        payload = await run_one(client, model, shuffled, "shuffled")
        payload["shuffle_order"] = shuffled
        out = OUT_DIR / f"{model.replace('/', '_')}__shuffled.json"
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        manifest["files"].append(str(out))

    (OUT_DIR / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2)
    )
    print(f"\nDone. Results in {OUT_DIR}/")


if __name__ == "__main__":
    asyncio.run(main())
