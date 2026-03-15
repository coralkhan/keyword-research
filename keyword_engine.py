import json
import time
import random
import sys
import argparse
import requests
import pandas as pd
import numpy as np
from pytrends.request import TrendReq


# ======================================
# CONFIGURATION
# ======================================

TARGET_KEYWORDS  = 120
REQUEST_DELAY    = 1.2
TREND_TIMEFRAME  = "today 12-m"
OUTPUT_TOP       = 20
TRENDS_BATCH     = 10
TRENDS_DELAY     = 5
BACKOFF_BASE     = 20
MAX_RETRIES      = 4

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


# ======================================
# ARGUMENT PARSER
# ======================================

def parse_args():
    parser = argparse.ArgumentParser(description="Blog Keyword Engine")
    parser.add_argument(
        "--seed",
        type=str,
        required=False,
        default=None,
        help="Seed keyword to research (e.g. 'gaming phones')"
    )
    parser.add_argument(
        "--output",
        type=str,
        required=False,
        default=None,
        help="Output filename prefix (e.g. 'gaming_phones'). Defaults to seed-based name."
    )
    return parser.parse_args()


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
# PYTRENDS SETUP
# ======================================

def make_pytrends():
    return TrendReq(hl="en-US", tz=330, timeout=(10, 30), retries=0, backoff_factor=0)

pytrends = make_pytrends()


# ======================================
# GOOGLE AUTOSUGGEST
# ======================================

def google_autosuggest(keyword):
    url    = "https://suggestqueries.google.com/complete/search"
    params = {"client": "firefox", "q": keyword}
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=10)
        r.raise_for_status()
        return r.json()[1]
    except Exception as e:
        print(f"    [autosuggest error] {keyword!r} -> {e}")
        return []


# ======================================
# KEYWORD COLLECTION
# ======================================

def collect_keywords(seed):
    keywords = set()
    visited  = set()
    base     = seed.split()[0]

    question_prefixes = [
        f"how to {seed}", f"what is {seed}", f"why {seed}",
        f"best {seed}", f"how does {seed} work",
        f"{seed} for beginners", f"{seed} tips",
        f"{seed} guide", f"{seed} tutorial",
    ]

    print(f"\nCollecting keywords for: '{seed}'\n")

    for term in [seed] + question_prefixes:
        if len(keywords) >= TARGET_KEYWORDS:
            break
        if term in visited:
            continue
        visited.add(term)

        new = 0
        for s in google_autosuggest(term):
            s = s.strip().lower()
            if s and s not in keywords and s != seed and base in s:
                keywords.add(s)
                new += 1
                if len(keywords) >= TARGET_KEYWORDS:
                    break

        print(f"  BFS '{term}' -> +{new}  (total: {len(keywords)})")
        time.sleep(REQUEST_DELAY)

    for letter in "abcdefghijklmnopqrstuvwxyz":
        if len(keywords) >= TARGET_KEYWORDS:
            break
        new = 0
        for s in google_autosuggest(f"{base} {letter}"):
            s = s.strip().lower()
            if s and s not in keywords and base in s:
                keywords.add(s)
                new += 1
                if len(keywords) >= TARGET_KEYWORDS:
                    break
        if new:
            print(f"  Alpha '{base} {letter}' -> +{new}  (total: {len(keywords)})")
        time.sleep(REQUEST_DELAY)

    result = list(keywords)[:TARGET_KEYWORDS]
    print(f"\nTotal keywords collected: {len(result)}")
    return result


# ======================================
# GOOGLE TRENDS (batch + exponential backoff)
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
        if "429" in str(e) or "Too Many Requests" in str(e):
            if attempt > MAX_RETRIES:
                print(f"\n    [trends] Blocked after {MAX_RETRIES} retries -- skipping batch.")
                return empty
            wait = BACKOFF_BASE * (2 ** (attempt - 1)) + random.uniform(0, 10)
            print(f"\n    [trends] 429 hit. Retry {attempt}/{MAX_RETRIES} -- waiting {wait:.0f}s ...")
            time.sleep(wait)
            pytrends = make_pytrends()
            return fetch_trends_batch(batch, attempt + 1)

        print(f"\n    [trends error] {batch} -> {e}")
        return empty


# ======================================
# BOOM SCORE
# ======================================

def boom_score(demand, momentum, word_count):
    return round(demand * 0.55 + max(-50.0, min(50.0, momentum)) * 0.30 + word_count * 3.0, 2)

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

    print(f"\nScoring {total} keywords in {len(batches)} batches (~{len(batches)*TRENDS_DELAY//60}-{len(batches)*TRENDS_DELAY//60+2} min)\n")

    results = []
    done    = 0

    for batch_num, batch in enumerate(batches, 1):
        for kw, td in fetch_trends_batch(batch).items():
            wc    = len(kw.split())
            score = boom_score(td["demand"], td["momentum"], wc)
            done += 1
            record = {
                "keyword": kw, "word_count": wc,
                "trend_demand": td["demand"], "trend_momentum": td["momentum"],
                "trend_status": td["trend_status"],
                "boom_score": score, "grade": grade(score),
            }
            results.append(record)
            filled = int((done / total) * 20)
            print(
                f"  [{'█'*filled}{'░'*(20-filled)}] {done:>3}/{total}  "
                f"score={score:>6.1f} ({record['grade']})  "
                f"demand={td['demand']:>5.1f}  "
                f"trend={td['trend_status']:<14}  {kw}"
            )

        pd.DataFrame(results).to_csv("keyword_progress.csv", index=False)
        with open("keyword_progress.json", "w") as f:
            json.dump(to_native(results), f, indent=2)

        if batch_num < len(batches):
            time.sleep(TRENDS_DELAY + random.uniform(-3, 5))

    df = pd.DataFrame(results).sort_values("boom_score", ascending=False).reset_index(drop=True)
    df["rank"] = range(1, len(df) + 1)
    return df


# ======================================
# SAVE RESULTS
# ======================================

def save(seed, df, output_prefix=None):
    name = output_prefix if output_prefix else seed.replace(" ", "_").lower()
    csv_file  = f"{name}_keywords.csv"
    json_file = f"{name}_keywords.json"

    df.to_csv(csv_file, index=False)
    with open(json_file, "w", encoding="utf-8") as f:
        json.dump(to_native(df.to_dict("records")), f, indent=2, ensure_ascii=False)

    print(f"\nSaved: {csv_file}  |  {json_file}")
    return csv_file, json_file


# ======================================
# PRINT SUMMARY
# ======================================

def print_summary(seed, df):
    print("\n" + "=" * 72)
    print(f"  TOP {OUTPUT_TOP} BOOM KEYWORDS FOR: '{seed}'")
    print("=" * 72)
    print(f"  {'#':<4} {'GRD':<5} {'SCORE':<8} {'DEMAND':<8} {'TREND':<15} KEYWORD")
    print("  " + "-" * 68)
    for _, row in df.head(OUTPUT_TOP).iterrows():
        print(f"  {int(row['rank']):<4} {row['grade']:<5} {row['boom_score']:<8.1f} {row['trend_demand']:<8.1f} {row['trend_status']:<15} {row['keyword']}")
    print("=" * 72)

    grade_counts = df["grade"].value_counts()
    print("\n  Grade breakdown:")
    for g in ["A+", "A", "B", "C", "D"]:
        count = grade_counts.get(g, 0)
        print(f"    {g}  {'#'*count:<40} {count}")

    rising = df[df["trend_status"].isin(["rising", "rising_fast"])].head(5)
    if not rising.empty:
        print("\n  Rising trends -- write these NOW:")
        for _, row in rising.iterrows():
            print(f"    [{row['trend_status']}]  {row['keyword']}")
    print()


# ======================================
# MAIN
# ======================================

def run():
    args = parse_args()

    if args.seed:
        # called from GitHub Actions or CLI with --seed flag
        seed = args.seed.strip()
        print(f"\n[CI mode] Seed keyword: '{seed}'")
    else:
        # interactive local mode
        print("\n" + "=" * 60)
        print("  BLOG KEYWORD ENGINE  --  Free, Honest, Actually Works")
        print("=" * 60 + "\n")
        seed = input("Enter main keyword: ").strip()

    if not seed:
        print("No keyword provided. Exiting.")
        sys.exit(1)

    df = analyze(seed)
    save(seed, df, output_prefix=args.output)
    print_summary(seed, df)


run()
