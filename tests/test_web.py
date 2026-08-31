import pytest
from fastapi.testclient import TestClient

from src.db import Database
from src.models import RuleHit, Signal
from src.web import create_app


@pytest.fixture
def client(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    db.save_signal(Signal("BTCUSDT", "1m", "LONG", False, 6.0, 100.0, None, None,
                          1000, [RuleHit("ema_cross", "LONG: x", 3.0)]))
    return TestClient(create_app(db, status_provider=lambda: {"symbols": 300, "ws": True}))


def test_status(client):
    r = client.get("/api/status")
    assert r.json()["symbols"] == 300


def test_signals(client):
    r = client.get("/api/signals")
    assert r.json()[0]["symbol"] == "BTCUSDT"


def test_index_html(client):
    r = client.get("/")
    assert r.status_code == 200 and "Sinyal Akışı" in r.text
