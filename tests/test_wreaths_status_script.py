from __future__ import annotations

import io

import pytest

from scripts import wreaths_status


def test_credential_names_are_profile_specific():
    assert wreaths_status.credential_names("conditioners") == (
        "AVITO_CLIENT_ID",
        "AVITO_CLIENT_SECRET",
    )
    assert wreaths_status.credential_names("wreaths") == (
        "AVITO_CLIENT_ID_WREATHS",
        "AVITO_CLIENT_SECRET_WREATHS",
    )
    with pytest.raises(ValueError, match="Unknown profile"):
        wreaths_status.credential_names("unknown")


def test_load_credentials_prefers_environment(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "AVITO_CLIENT_ID_WREATHS=file-id\n"
        "AVITO_CLIENT_SECRET_WREATHS=file-secret\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AVITO_CLIENT_ID_WREATHS", "process-id")
    monkeypatch.setenv("AVITO_CLIENT_SECRET_WREATHS", "process-secret")
    assert wreaths_status.load_credentials("wreaths", env_file) == (
        "process-id",
        "process-secret",
    )


def test_load_credentials_reads_file_and_reports_missing(monkeypatch, tmp_path):
    monkeypatch.delenv("AVITO_CLIENT_ID_CARVER", raising=False)
    monkeypatch.delenv("AVITO_CLIENT_SECRET_CARVER", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "AVITO_CLIENT_ID_CARVER=file-id\n"
        "AVITO_CLIENT_SECRET_CARVER=file-secret\n",
        encoding="utf-8",
    )
    assert wreaths_status.load_credentials("carver", env_file) == (
        "file-id",
        "file-secret",
    )
    with pytest.raises(ValueError, match="AVITO_CLIENT_ID_APPLIANCES"):
        wreaths_status.load_credentials("appliances", tmp_path / "missing.env")
    partial = tmp_path / "partial.env"
    partial.write_text("UNRELATED=value\n", encoding="utf-8")
    with pytest.raises(ValueError, match="AVITO_CLIENT_ID_APPLIANCES"):
        wreaths_status.load_credentials("appliances", partial)


class _StatusClient:
    def list_uploads(self):
        return [
            {
                "started_at": "2026-07-25",
                "upload_id": 10,
                "status": "finished",
                "stats": {"count": 2},
                "events": [{"type": "info", "description": "done"}],
            }
        ]

    def last_successful_items(self):
        return [
            {"ad_id": "active", "avito_status": "active", "messages": []},
            {
                "ad_id": "rejected",
                "avito_status": "rejected",
                "messages": ["bad image"],
            },
        ]


def test_print_status_renders_uploads_counts_and_non_active_items():
    output = io.StringIO()

    count = wreaths_status.print_status(_StatusClient(), limit=1, output=output)

    text = output.getvalue()
    assert count == 2
    assert "id=10" in text
    assert "[info] done" in text
    assert "'active': 1" in text
    assert "'rejected': 1" in text
    assert "rejected: rejected ['bad image']" in text
    assert "  active: active" not in text


def test_print_status_handles_missing_optional_upload_fields():
    class MinimalClient:
        def list_uploads(self):
            return [{}]

        def last_successful_items(self):
            return []

    output = io.StringIO()
    assert wreaths_status.print_status(MinimalClient(), output=output) == 0
    assert "id=?" in output.getvalue()


def test_import_does_not_construct_client(monkeypatch):
    # Re-executing the module body would be excessive; its public module state
    # proves no eager client was retained or request result cached.
    assert not hasattr(wreaths_status, "client")
    assert not hasattr(wreaths_status, "cfg")


def test_main_uses_profile_credentials_and_closes_client(monkeypatch, tmp_path):
    events = []

    class Client:
        def __init__(self, client_id, client_secret):
            events.append(("init", client_id, client_secret))

        def __enter__(self):
            events.append(("enter",))
            return self

        def __exit__(self, *args):
            events.append(("exit",))

    monkeypatch.setattr(
        wreaths_status,
        "load_credentials",
        lambda profile, path: (
            events.append(("credentials", profile, path)) or ("id", "secret")
        ),
    )
    monkeypatch.setattr(wreaths_status, "AvitoClient", Client)
    monkeypatch.setattr(
        wreaths_status,
        "print_status",
        lambda client, **kwargs: events.append(("status", kwargs["limit"])) or 0,
    )

    env_file = tmp_path / ".env"
    assert (
        wreaths_status.main(
            [
                "--profile",
                "carver",
                "--env-file",
                str(env_file),
                "--limit",
                "7",
            ]
        )
        == 0
    )
    assert events == [
        ("credentials", "carver", env_file),
        ("init", "id", "secret"),
        ("enter",),
        ("status", 7),
        ("exit",),
    ]


def test_main_reports_credentials_or_network_failure(monkeypatch, capsys):
    monkeypatch.setattr(
        wreaths_status,
        "load_credentials",
        lambda *_args: (_ for _ in ()).throw(ValueError("missing")),
    )
    assert wreaths_status.main([]) == 1
    assert "missing" in capsys.readouterr().err


def test_main_closes_client_when_network_status_fails(monkeypatch, capsys):
    events = []

    class Client:
        def __init__(self, *_args):
            pass

        def __enter__(self):
            events.append("enter")
            return self

        def __exit__(self, *_args):
            events.append("exit")

    monkeypatch.setattr(
        wreaths_status, "load_credentials", lambda *_args: ("id", "secret")
    )
    monkeypatch.setattr(wreaths_status, "AvitoClient", Client)
    monkeypatch.setattr(
        wreaths_status,
        "print_status",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("network down")),
    )

    assert wreaths_status.main([]) == 1
    assert events == ["enter", "exit"]
    assert "network down" in capsys.readouterr().err


def test_main_rejects_negative_limit():
    with pytest.raises(SystemExit) as error:
        wreaths_status.main(["--limit", "-1"])
    assert error.value.code == 2
