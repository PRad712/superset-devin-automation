"""Submit sample issue payloads to the manual trigger endpoint."""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def main() -> int:
    script_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--issues", type=Path, default=script_dir / "sample_issues.json")
    parser.add_argument("--delay", type=float, default=2.0)
    parser.add_argument("--token", default=os.environ.get("TRIGGER_TOKEN"))
    args = parser.parse_args()
    issues = json.loads(args.issues.read_text(encoding="utf-8"))
    failed = False
    for index, issue in enumerate(issues):
        payload = {
            "title": issue["title"],
            "body": issue.get("body", ""),
            "repo_full_name": issue.get("repo_full_name"),
        }
        request = urllib.request.Request(
            f"{args.url.rstrip('/')}/trigger/{issue['issue_number']}",
            data=json.dumps(payload).encode(),
            method="POST",
            headers={
                "Content-Type": "application/json",
                **({"X-Trigger-Token": args.token} if args.token else {}),
            },
        )
        try:
            with urllib.request.urlopen(request) as response:
                status = response.status
                response_body = response.read().decode()
        except urllib.error.HTTPError as error:
            status = error.code
            response_body = error.read().decode()
        print(status, issue["issue_number"], response_body)
        failed = failed or not 200 <= status < 300
        if index < len(issues) - 1 and args.delay:
            time.sleep(args.delay)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
