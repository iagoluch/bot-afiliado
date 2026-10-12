from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "deploy/systemd/bot-afiliado-shopee-discovery.service"
TIMER = ROOT / "deploy/systemd/bot-afiliado-shopee-discovery.timer"


def test_shopee_discovery_oneshot_cannot_publish():
    value=SERVICE.read_text(encoding="utf-8")
    assert "Type=oneshot" in value
    assert "shopee-affiliate-discover" in value
    assert "--stage" in value
    assert "--limit 5" in value
    assert "TELEGRAM_REAL_ENABLED" not in value
    assert "DRY_RUN=false" not in value
    assert "add-offer" not in value
    assert "bash" not in value
    assert "curl" not in value
    assert "WorkingDirectory=%h/bot-afiliado" in value


def test_shopee_discovery_timer_explicitly_scheduled():
    value=TIMER.read_text(encoding="utf-8")
    assert "OnCalendar=*-*-* 00,06,12,18:15:00" in value
    assert "Persistent=true" in value
    assert "RandomizedDelaySec=5m" in value
    assert "Unit=bot-afiliado-shopee-discovery.service" in value
    assert "WantedBy=timers.target" in value
