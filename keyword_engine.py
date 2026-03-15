import json
import time
import random
import argparse
import requests
import pandas as pd
import numpy as np
from pytrends.request import TrendReq


# ======================================
# CONFIG
# ======================================

TARGET_KEYWORDS = 80
REQUEST_DELAY   = 1.2
TREND_TIMEFRAME = "today 12-m"
TRENDS_BATCH    = 5
TRENDS_DELAY    = 20
BACKOFF_BASE    = 60
MAX_RETRIES     = 4

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


# ======================================
# JSON SAFE CONVERTER
# ======================================

def to_native(obj):
    if isinstance(obj, dict):
        return {k: to_native(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [to_native(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    return obj


# ======================================
# PYTRENDS
# ======================================

def make_pytrends():
    return TrendReq(hl="en-US", tz=330, timeout=(10, 30), retries=0, backoff_factor=0)

pytrends = make_pytrends()


# ======================================
# AUTOSUGGEST
# ======================================

def google_autosuggest(keyword):
    url    = "https://suggestqueries.google.com/complete/search"
    params = {"client": "firefox", "q": keyword}
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=10)
        r.raise_for_status()
        return r.json()[1]
    except Exception as e:
        print(f"  [autosuggest error] {keyword!r} -> {e}")
        return []


# ======================================
# KEYWORD COLLECTION
# ======================================

def collect_keywords(seed):
    keywords = set()
    visited  = set()

    prefixes = [
        seed,
        f"how to {seed}", f"what is {seed}", f"why {seed}",
        f"best {seed}", f"how does {seed} work",
        f"{seed} for beginners", f"{seed} tips",
        f"{seed} guide", f"{seed} tutorial",
    ]

    print(f"Collecting keywords for: '{seed}'")

    for term in prefixes:
        if len(keywords) >= TARGET_KEYWORDS:
            break
        if term in visited:
            continue
        visited.add(term)
        suggestions = google_autosuggest(term)
        new = 0
        for s in suggestions:
            s = s.strip().lower()
            # filter: seed word must appear in suggestion
            if s and s not in keywords and s != seed and seed.split()[0] in s:
                keywords.add(s)
                new += 1
                if len(keywords) >= TARGET_KEYWORDS:
                    break
        print(f"  BFS '{term}' -> +{new} (total: {len(keywords)})")
        time.sleep(REQUEST_DELAY)

    base = seed.split()[0]
    for letter in "abcdefghijklmnopqrstuvwxyz":
        if len(keywords) >= TARGET_KEYWORDS:
            break
        suggestions = google_autosuggest(f"{base} {letter}")
        new = 0
        for s in suggestions:
            s = s.strip().lower()
            if s and s not in keywords and base in s:
                keywords.add(s)
                new += 1
                if len(keywords) >= TARGET_KEYWORDS:
                    break
        if new:
            print(f"  Alpha '{base} {letter}' -> +{new} (total: {len(keywords)})")
        time.sleep(REQUEST_DELAY)

    result = list(keywords)[:TARGET_KEYWORDS]
    print(f"Total collected: {len(result)}")
    return result


# ======================================
# TRENDS BATCH
# ======================================

def fetch_trends_batch(batch, attempt=1):
    global pytrends
    empty = {kw: {"demand": 0.0, "momentum": 0.0, "trend_status": "no_data"} for kw in batch}

    try:
        pytrends.build_payload(batch, timeframe=TREND_TIMEFRAME, geo="")
        data = pytrends.interest_over_time()
        if data.empty:
            return empty

        result = {}
        for kw in batch:
            if kw not in data.columns:
                result[kw] = empty[kw]
                continue
            values = data[kw].dropna()
            if len(values) == 0:
                result[kw] = empty[kw]
                continue

            demand = round(float(values.mean()), 2)
            if len(values) >= 8:
                momentum = round(float(values.iloc[-4:].mean()) - float(values.iloc[:4].mean()), 2)
            else:
                momentum = round(float(values.iloc[-1] - values.iloc[0]), 2)

            if   momentum >  15: status = "rising_fast"
            elif momentum >   5: status = "rising"
            elif momentum < -15: status = "falling_fast"
            elif momentum <  -5: status = "falling"
            else:                status = "stable"

            result[kw] = {"demand": demand, "momentum": momentum, "trend_status": status}
        return result

    except Exception as e:
        err = str(e)
        if "429" in err or "Too Many Requests" in err.lower():
            if attempt > MAX_RETRIES:
                print(f"  [trends] Blocked after {MAX_RETRIES} retries. Skipping batch.")
                return empty
            wait = BACKOFF_BASE * (2 ** (attempt - 1)) + random.uniform(0, 10)
            print(f"  [trends] 429 hit. Retry {attempt}/{MAX_RETRIES} - waiting {wait:.0f}s...")
            time.sleep(wait)
            pytrends = make_pytrends()
            return fetch_trends_batch(batch, attempt + 1)
        print(f"  [trends error] {batch} -> {e}")
        return empty


# ======================================
# SCORING
# ======================================

def boom_score(demand, momentum, word_count):
    momentum_capped = max(-50.0, min(50.0, momentum))
    return round(demand * 0.55 + momentum_capped * 0.30 + word_count * 3.0, 2)

def grade(score):
    if score >= 70: return "A+"
    if score >= 55: return "A"
    if score >= 40: return "B"
    if score >= 25: return "C"
    return "D"


# ======================================
# ANALYSIS
# ======================================

def analyze(seed):
    keywords = collect_keywords(seed)
    total    = len(keywords)
    batches  = [keywords[i:i+TRENDS_BATCH] for i in range(0, total, TRENDS_BATCH)]

    print(f"\nScoring {total} keywords in {len(batches)} batches...")
    results = []
    done    = 0

    for batch_num, batch in enumerate(batches, 1):
        td_map = fetch_trends_batch(batch)
        for kw in batch:
            td    = td_map[kw]
            wc    = len(kw.split())
            score = boom_score(td["demand"], td["momentum"], wc)
            done += 1
            record = {
                "keyword"        : kw,
                "word_count"     : wc,
                "trend_demand"   : td["demand"],
                "trend_momentum" : td["momentum"],
                "trend_status"   : td["trend_status"],
                "boom_score"     : score,
                "grade"          : grade(score),
            }
            results.append(record)
            print(f"  [{done:>3}/{total}] score={score:>6.1f} ({record['grade']})  demand={td['demand']:>5.1f}  {td['trend_status']:<14}  {kw}")

        if batch_num < len(batches):
            time.sleep(TRENDS_DELAY + random.uniform(-3, 5))

    df = pd.DataFrame(results)
    df = df.sort_values(by="boom_score", ascending=False).reset_index(drop=True)
    df["rank"] = range(1, len(df) + 1)
    return df


# ======================================
# MAIN (CLI)
# ======================================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed",   required=True, help="Seed keyword")
    parser.add_argument("--output", required=True, help="Output JSON file path")
    args = parser.parse_args()

    df = analyze(args.seed)

    output = {
        "seed"    : args.seed,
        "total"   : len(df),
        "keywords": to_native(df.to_dict("records"))
    }

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\nResults saved to {args.output}")
    print(f"Top keyword: {df.iloc[0]['keyword']} (score: {df.iloc[0]['boom_score']})")


if __name__ == "__main__":
    main()
