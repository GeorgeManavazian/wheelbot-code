import json
from live import alerts


def _cfg(tmp_path):
    p = tmp_path / "alerts.json"
    p.write_text(json.dumps({
        "smtp_host": "smtp.example.com", "smtp_port": 587,
        "username": "u@example.com", "password": "pw",
        "from_addr": "u@example.com", "to_addr": "u@example.com"}))
    return str(p)


def test_missing_config_returns_none(tmp_path):
    assert alerts.load_alert_config(str(tmp_path / "nope.json")) is None


def test_send_alert_without_config_returns_false_and_does_not_raise(tmp_path):
    assert alerts.send_alert("subj", "body", path=str(tmp_path / "nope.json")) is False


def test_send_alert_success(tmp_path, monkeypatch):
    sent = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            sent["host"], sent["port"] = host, port

        def starttls(self):
            sent["tls"] = True

        def login(self, u, p):
            sent["login"] = (u, p)

        def send_message(self, m):
            sent["msg"] = m

        def quit(self):
            sent["quit"] = True

    monkeypatch.setattr(alerts.smtplib, "SMTP", FakeSMTP)
    assert alerts.send_alert("token stale", "6 days", path=_cfg(tmp_path)) is True
    assert sent["host"] == "smtp.example.com" and sent["tls"] is True
    assert sent["msg"]["Subject"] == "Wheel Bot: token stale"
    assert sent["msg"]["To"] == "u@example.com"


def test_smtp_failure_is_swallowed(tmp_path, monkeypatch):
    """A broken mailer must never abort a trading run."""
    def boom(*a, **k):
        raise OSError("network unreachable")
    monkeypatch.setattr(alerts.smtplib, "SMTP", boom)
    assert alerts.send_alert("subj", "body", path=_cfg(tmp_path)) is False


def test_malformed_config_returns_none(tmp_path):
    p = tmp_path / "alerts.json"
    p.write_text("{not json")
    assert alerts.load_alert_config(str(p)) is None
