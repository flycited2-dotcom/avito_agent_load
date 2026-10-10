import json

from avito_bridge.ready_price.server_run import publication_pause
from avito_bridge.ready_price.publish import publish_ready_content


def test_owner_pause_and_resume_cannot_override_audit(tmp_path):
    state = tmp_path / "state/ready-price"
    state.mkdir(parents=True)
    control = state / "publication-control.json"
    assert publication_pause(tmp_path) is None
    control.write_text(json.dumps({"enabled": False}))
    assert publication_pause(tmp_path) == "paused_by_owner"
    control.write_text(json.dumps({"enabled": True}))
    assert publication_pause(tmp_path) is None
    (state / "content-publication.paused").write_text(json.dumps({"reason": "owner_requested_stock_audit"}))
    assert publication_pause(tmp_path) == "paused_stock_audit"


def test_invalid_control_fails_closed(tmp_path):
    state = tmp_path / "state/ready-price"
    state.mkdir(parents=True)
    control = state / "publication-control.json"
    for payload in ('{"enabled":"false"}', '[]', '{'):
        control.write_text(payload)
        assert publication_pause(tmp_path) == "paused_invalid_owner_control"


def test_publisher_itself_honors_owner_stop_before_database_or_content_work(tmp_path):
    state = tmp_path / "state/ready-price"
    state.mkdir(parents=True)
    (state / "publication-control.json").write_text('{"enabled":false}')
    missing = tmp_path / "not-created"
    result = publish_ready_content(missing, missing, missing, tmp_path, missing, missing, missing, [], [])
    assert result["status"] == "paused_by_owner" and result["added"] == []
    assert not missing.exists()
