import json
import argparse
import requests
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--results",  required=True, help="Path to results JSON file")
    parser.add_argument("--callback", required=True, help="Apps Script webhook URL")
    parser.add_argument("--seed",     required=True, help="Original seed keyword")
    args = parser.parse_args()

    with open(args.results, "r", encoding="utf-8") as f:
        data = json.load(f)

    payload = {
        "seed"    : args.seed,
        "total"   : data["total"],
        "keywords": data["keywords"]   # full ranked list
    }

    print(f"POSTing {data['total']} keywords back to Apps Script...")
    print(f"Callback URL: {args.callback[:60]}...")

    try:
        r = requests.post(
            args.callback,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=30
        )
        print(f"Response status: {r.status_code}")
        print(f"Response body: {r.text[:200]}")

        if r.status_code not in (200, 201, 302):
            print(f"WARNING: Unexpected status {r.status_code}")
            sys.exit(1)

    except Exception as e:
        print(f"ERROR posting results: {e}")
        sys.exit(1)

    print("Done.")


if __name__ == "__main__":
    main()
