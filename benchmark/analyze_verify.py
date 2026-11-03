#!/usr/bin/env python3
"""Метрики verify-бенчмарка: precision/recall/κ + bias probe."""

import difflib
import json
import random
import sys
from pathlib import Path

GOLD = Path("benchmark/gold_verify_na_shtile.json")
RES = Path("benchmark/verify_results")
LABELS = ("confirmed", "rejected")


def text_sim(a, b):
    return difflib.SequenceMatcher(None, (a or "").lower(), (b or "").lower()).ratio()


def match_all(gold_items, results, threshold=0.4):
    """Greedy unique bipartite match by text similarity."""
    candidates = []
    for i, r in enumerate(results):
        if r.get("status") in ("unparsed", "error"):
            continue
        for j, g in enumerate(gold_items):
            sc = text_sim(g["finding_text"], r.get("text"))
            if sc >= threshold:
                candidates.append((sc, i, j))
    candidates.sort(reverse=True)
    used_r, used_g = set(), set()
    matches = []
    for sc, i, j in candidates:
        if i in used_r or j in used_g:
            continue
        matches.append((i, j, sc))
        used_r.add(i)
        used_g.add(j)
    return matches


def kappa(y1, y2):
    n = len(y1)
    if n == 0:
        return 0.0
    po = sum(1 for a, b in zip(y1, y2) if a == b) / n
    pe = sum(
        (sum(1 for a in y1 if a == label) / n) * (sum(1 for a in y2 if a == label) / n)
        for label in LABELS
    )
    return (po - pe) / (1 - pe) if pe < 1 else 0.0


def pr(y_true, y_pred):
    tp = sum(1 for t, p in zip(y_true, y_pred) if t == "confirmed" and p == "confirmed")
    fp = sum(1 for t, p in zip(y_true, y_pred) if t != "confirmed" and p == "confirmed")
    fn = sum(1 for t, p in zip(y_true, y_pred) if t == "confirmed" and p != "confirmed")
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return precision, recall, tp, fp, fn


def boot(y_true, y_pred, fn, n=2000, alpha=0.05):
    rng = random.Random(42)
    vals = []
    for _ in range(n):
        idx = [rng.randrange(len(y_true)) for _ in range(len(y_true))]
        vals.append(fn([y_true[i] for i in idx], [y_pred[i] for i in idx]))
    vals.sort()
    return vals[int(n * alpha / 2)], vals[int(n * (1 - alpha / 2))]


def evaluate(results, gold_items):
    matches = match_all(gold_items, results)
    matched_idx = {i for i, _, _ in matches}
    unparsed = sum(1 for r in results if r.get("status") == "unparsed")
    errors = sum(1 for r in results if r.get("status") == "error")
    unmatched = sum(
        1
        for i, r in enumerate(results)
        if r.get("status") not in ("unparsed", "error") and i not in matched_idx
    )
    pairs = []
    nonbinary = 0
    match_log = []
    for i, j, sc in sorted(matches):
        st = results[i].get("status")
        ml = gold_items[j]["manual_label"]
        if st in LABELS:
            pairs.append((ml, st))
            mark = "\u2713" if st == ml else "\u2717"
            match_log.append(
                f"    {mark} [{i:2d}]\u2192{gold_items[j]['id']:4s} "
                f"s={sc:.2f} status={st:9s} manual={ml}"
            )
        else:
            nonbinary += 1
            match_log.append(
                f"    ? [{i:2d}]\u2192{gold_items[j]['id']:4s} "
                f"s={sc:.2f} status={st} (nonbinary)"
            )
    base = {
        "n_matched": len(matches),
        "n_pairs": len(pairs),
        "unparsed": unparsed,
        "errors": errors,
        "unmatched": unmatched,
        "nonbinary": nonbinary,
        "matches": match_log,
    }
    if not pairs:
        return base
    yt = [p[0] for p in pairs]
    yp = [p[1] for p in pairs]
    p, r, tp, fp, fn = pr(yt, yp)
    p_lo, p_hi = boot(yt, yp, lambda a, b: pr(a, b)[0])
    r_lo, r_hi = boot(yt, yp, lambda a, b: pr(a, b)[1])
    return {
        **base,
        "precision": round(p, 3),
        "precision_ci95": [round(p_lo, 3), round(p_hi, 3)],
        "recall": round(r, 3),
        "recall_ci95": [round(r_lo, 3), round(r_hi, 3)],
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "cohen_kappa": round(kappa(yt, yp), 3),
        "raw_agreement": round(sum(1 for a, b in pairs if a == b) / len(pairs), 3),
    }


def main():
    gold = json.loads(GOLD.read_text())
    gold_items = [g for g in gold if g.get("manual_label") in LABELS]
    if not gold_items:
        sys.exit("manual_label не заполнен")
    nc = sum(1 for g in gold_items if g["manual_label"] == "confirmed")
    nr = len(gold_items) - nc
    print(f"gold: {len(gold_items)} (confirmed={nc}, rejected={nr})")

    summary = {}
    for f in sorted(RES.glob("*__*.json")):
        if f.name == "manifest.json":
            continue
        d = json.loads(f.read_text())
        m = evaluate(d.get("results", []), gold_items)
        log = m.pop("matches", [])
        summary[f.name] = {"model": d.get("model"), "tag": d.get("tag"), **m}
        print(f"\n=== {f.name} ===")
        for line in log:
            print(line)
        print(json.dumps(m, ensure_ascii=False))

    print("\n=== Aggregate per model (repeats only) ===")
    by_model = {}
    for s in summary.values():
        if s.get("tag") == "shuffled" or "precision" not in s:
            continue
        by_model.setdefault(s["model"], []).append(s)
    for model, runs in by_model.items():
        ps = [r["precision"] for r in runs]
        rs = [r["recall"] for r in runs]
        ks = [r["cohen_kappa"] for r in runs]
        fps = [r["fp"] for r in runs]
        print(f"\n{model}:")
        print(
            f"  precision runs={[round(x, 3) for x in ps]}  mean={sum(ps) / len(ps):.3f}"
        )
        print(
            f"  recall    runs={[round(x, 3) for x in rs]}  mean={sum(rs) / len(rs):.3f}"
        )
        print(
            f"  kappa     runs={[round(x, 3) for x in ks]}  mean={sum(ks) / len(ks):.3f}"
        )
        print(f"  FP        runs={fps}")

    print("\n=== Bias probe (shuffled vs rep0) ===")
    for model in {s["model"] for s in summary.values()}:
        rep0 = next(
            (s for s in summary.values() if s["model"] == model and s["tag"] == "rep0"),
            None,
        )
        shuf = next(
            (
                s
                for s in summary.values()
                if s["model"] == model and s["tag"] == "shuffled"
            ),
            None,
        )
        if not rep0 or not shuf:
            continue
        for k in ("precision", "recall", "cohen_kappa"):
            if k in rep0 and k in shuf:
                delta = abs(rep0[k] - shuf[k])
                flag = "OK" if delta <= 0.15 else "SHIFT"
                print(
                    f"  {model} {k}: {rep0[k]:.3f} → {shuf[k]:.3f}  "
                    f"Δ={delta:.3f} {flag}"
                )


if __name__ == "__main__":
    main()
