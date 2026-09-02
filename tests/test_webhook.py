import hashlib
import hmac

from app.webhook import extract_remediation_issue, verify_signature


def test_signature_valid_invalid_and_missing():
    body = b'{"hello":"world"}'
    digest = hmac.new(b"secret", body, hashlib.sha256).hexdigest()
    assert verify_signature("secret", body, f"sha256={digest}")
    assert not verify_signature("secret", body, "sha256=wrong")
    assert not verify_signature("secret", body, None)


def _payload(action="opened", label_name="devin-remediate"):
    payload = {
        "action": action,
        "issue": {"number": 42, "title": "Title", "body": "Body"},
        "repository": {"full_name": "org/repo"},
    }
    if action == "labeled":
        payload["label"] = {"name": label_name}
    return payload


def test_extract_opened_and_labeled_events():
    assert extract_remediation_issue(_payload(), "devin-remediate").number == 42
    assert extract_remediation_issue(_payload("labeled"), "devin-remediate").title == "Title"
    assert extract_remediation_issue(_payload("labeled", "other"), "devin-remediate") is None
    assert extract_remediation_issue(_payload("closed"), "devin-remediate") is None


def test_pull_request_payload_is_ignored():
    payload = _payload()
    payload["issue"]["pull_request"] = {"url": "https://github.com/org/repo/pull/1"}
    assert extract_remediation_issue(payload, "devin-remediate") is None
