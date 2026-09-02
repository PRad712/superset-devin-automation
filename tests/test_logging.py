import json
import logging

from app.logging_config import JsonFormatter, log_event


def test_json_formatter_contains_event_and_fields(capsys):
    logger = logging.getLogger("test.json")
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    log_event(logger, "session_created", event="github", issue_number=42, repo="org/repo")
    output = capsys.readouterr().err
    payload = json.loads(output)
    assert payload["event"] == "session_created"
    assert payload["message"] == "session_created"
    assert payload["issue_number"] == 42
    assert payload["repo"] == "org/repo"
