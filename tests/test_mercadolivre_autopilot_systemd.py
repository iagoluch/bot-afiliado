from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_browser_dependency_is_optional():
    content=(ROOT/"requirements-browser.txt").read_text()
    assert "playwright" in content
    base=(ROOT/"requirements.txt").read_text()
    assert "playwright" not in base


def test_browser_profile_and_config_cannot_enter_git():
    content=(ROOT/".gitignore").read_text()
    assert "data/ml_browser_profile/" in content
    assert "data/ml_autopilot_config.json" in content


def test_ui_job_scheduled_at_most_four_times_per_day():
    service=(ROOT/"deploy/systemd/bot-afiliado-ml-autopilot.service").read_text()
    timer=(ROOT/"deploy/systemd/bot-afiliado-ml-autopilot.timer").read_text()
    assert "app.mercadolivre_autopilot once" in service
    assert "Type=oneshot" in service
    assert "OnCalendar=*-*-* 00,06,12,18:25:00" in timer
    assert "RandomizedDelaySec=7m" in timer
    assert "Persistent=true" in timer
    assert "shopee" not in service.lower()
    assert "TELEGRAM_REAL_ENABLED=true" not in service


def test_worker_systemd_uses_existing_safe_publish_pipeline():
    content=(ROOT/"deploy/systemd/bot-afiliado-telegram-worker.service").read_text()
    assert "python -m app.worker" in content
    assert "Restart=on-failure" in content
    assert "app.mercadolivre_autopilot" not in content


def test_playwright_supports_ubuntu_2604():
    """Regression for Acer: Playwright 1.55 fails on ubuntu26.04-x64."""
    import re
    content = (ROOT / "requirements-browser.txt").read_text(encoding="utf-8").strip()
    match = re.fullmatch(r"playwright==(\d+)\.(\d+)\.(\d+)", content)
    assert match is not None
    assert tuple(map(int, match.groups())) >= (1, 61, 0)
