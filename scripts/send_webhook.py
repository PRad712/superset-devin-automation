"""Send a GitHub issues webhook payload to the local service."""

import argparse
import hashlib
import hmac
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("payload", type=Path)
    parser.add_argument("--secret", default=os.environ.get("GITHUB_WEBHOOK_SECRET"))
    parser.add_argument("--url", default="http://localhost:8000/webhook/github")
    args = parser.parse_args()
    if not args.secret:
        parser.error("--secret or GITHUB_WEBHOOK_SECRET is required")
    body = args.payload.read_bytes()
    digest = hmac.new(args.secret.encode(), body, hashlib.sha256).hexdigest()
    request = urllib.request.Request(
        args.url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": f"sha256={digest}",
            "X-GitHub-Event": "issues",
        },
    )
    try:
        with urllib.request.urlopen(request) as response:
            print(response.status, response.read().decode())
    except urllib.error.HTTPError as error:
        print(error.code, error.read().decode())
    return 0


if __name__ == "__main__":
    sys.exit(main())
