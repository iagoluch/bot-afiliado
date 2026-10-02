from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from urllib.parse import parse_qs

import pytest

from app.cli import main as cli_main
from app.config import Settings
from app.db import Database
from app.models import Offer
from app.services.compliance import ComplianceError
from app.services.instagram_graph import (
    InstagramGraphClient,
    InstagramGraphError,
    InstagramReelPublisher,
    _NoRedirectHandler,
)


ASSET_URL = "https://media.example/media/offer-1/instagram-reel.mp4"


def settings_for(tmp_path: Path, *, dry_run: bool = False, **changes) -> Settings:
    settings = Settings(
        database_path=tmp_path / "instagram.db",
        dry_run=dry_run,
        public_base_url="https://offers.example",
        telegram_bot_token=None,
        telegram_chat_id=None,
        creatives_path=tmp_path / "creatives",
        instagram_access_token="meta-secret-token",
        instagram_user_id="17841400000000001",
        instagram_graph_api_version="v25.0",
        instagram_media_base_url="https://media.example/media",
        instagram_asset_allowed_hosts=("media.example",),
        instagram_container_api_enabled=True,
        instagram_facebook_login_ready=True,
    )
    return replace(settings, **changes)


def create_reel_queue(db: Database, settings: Settings, *, format: str = "reel", status: str = "READY_FOR_PUBLISH") -> int:
    offer_id = db.upsert_offer(Offer(
        merchant="Shopee",
        affiliate_network="Shopee Afiliados",
        external_product_id=f"instagram-{format}",
        title="Produto para Instagram",
        current_price_cents=9990,
        source_url="https://shopee.com.br/product/1/2",
        affiliate_url="https://s.shopee.com.br/official-link",
        stock_status="IN_STOCK",
    ))
    relative = f"offer-{offer_id}/instagram-{format}.mp4"
    asset = settings.creatives_path / relative
    asset.parent.mkdir(parents=True, exist_ok=True)
    asset.write_bytes(b"\x00\x00\x00\x18ftypmp42test")
    content_id = db.add_content(
        offer_id,
        "instagram",
        "#publi\nConfira os detalhes em https://offers.example/o/produto",
        format=format,
        payload={"disclosure": "#publi"},
        assets=[relative],
        campaign_id="instagram-test",
    )
    return db.enqueue_social(offer_id, content_id, "instagram", format, status, "Revisar")


class FakeResponse:
    def __init__(self, payload: dict):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self, limit: int) -> bytes:
        return self.payload[:limit]


def test_graph_contract_uses_fixed_origin_bearer_header_and_no_redirect() -> None:
    requests = []
    responses = iter((
        {"id": "18000000000000001"},
        {"status_code": "FINISHED", "status": "Finished"},
    ))

    def opener(request, timeout):
        requests.append((request, timeout))
        return FakeResponse(next(responses))

    client = InstagramGraphClient("super-secret", "v25.0", opener=opener)
    container = client.create_reel("17841400000000001", ASSET_URL, "#publi\nOferta")
    assert client.container_status(container) == "FINISHED"

    assert [request.method for request, _ in requests] == ["POST", "GET"]
    assert requests[0][0].full_url == "https://graph.facebook.com/v25.0/17841400000000001/media"
    assert requests[1][0].full_url.endswith(
        "/18000000000000001?fields=status_code%2Cstatus"
    )
    first_body = parse_qs(requests[0][0].data.decode("utf-8"))
    assert first_body == {
        "media_type": ["REELS"],
        "video_url": [ASSET_URL],
        "caption": ["#publi\nOferta"],
        "share_to_feed": ["true"],
    }
    assert all("media_publish" not in request.full_url for request, _ in requests)
    for request, timeout in requests:
        assert timeout == 20.0
        assert request.get_header("Authorization") == "Bearer super-secret"
        assert "super-secret" not in request.full_url
        assert not request.data or b"super-secret" not in request.data
    assert _NoRedirectHandler().redirect_request(None, None, 302, "Found", {}, "https://evil.example") is None


class FakeGraphClient:
    def __init__(self, *, status: str = "FINISHED"):
        self.status = status
        self.create_calls = 0
        self.status_calls = 0
        self.publish_calls = 0

    def create_reel(self, ig_user_id: str, video_url: str, caption: str) -> str:
        self.create_calls += 1
        assert ig_user_id == "17841400000000001"
        assert video_url == ASSET_URL
        assert caption.startswith("#publi")
        return "18000000000000001"

    def container_status(self, container_id: str) -> str:
        self.status_calls += 1
        assert container_id == "18000000000000001"
        return self.status

    def publish(self, ig_user_id: str, container_id: str) -> str:
        self.publish_calls += 1
        raise AssertionError("media_publish nao pode ser chamado")


def test_container_creation_is_reserved_before_remote_call(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    db = Database(settings.database_path)
    db.init()
    queue_id = create_reel_queue(db, settings)

    class SecondClient:
        calls = 0

        def create_reel(self, *_args):
            self.calls += 1
            raise AssertionError("segunda chamada remota nao pode ocorrer")

    second_client = SecondClient()
    second = InstagramReelPublisher(db, settings, client=second_client)

    class FirstClient:
        calls = 0

        def create_reel(self, *_args):
            self.calls += 1
            attempt = db.instagram_container_attempt(queue_id)
            assert attempt is not None
            assert attempt["status"] == "CREATING"
            with pytest.raises(ComplianceError, match="andamento ou exige reconciliacao"):
                second.create_reel(queue_id, approved=True)
            return "18000000000000001"

    first_client = FirstClient()
    result = InstagramReelPublisher(db, settings, client=first_client).create_reel(
        queue_id,
        approved=True,
    )

    assert result["status"] == "CONTAINER_CREATED"
    assert first_client.calls == 1
    assert second_client.calls == 0
    assert db.instagram_container_attempt(queue_id) is None


def test_ambiguous_container_creation_blocks_retry_until_manual_reconciliation(
    tmp_path: Path,
) -> None:
    settings = settings_for(tmp_path)
    db = Database(settings.database_path)
    db.init()
    queue_id = create_reel_queue(db, settings)

    class AmbiguousClient:
        def create_reel(self, *_args):
            raise InstagramGraphError(
                "resultado incerto",
                code="TRANSPORT_FAILURE",
                ambiguous=True,
            )

    publisher = InstagramReelPublisher(db, settings, client=AmbiguousClient())
    with pytest.raises(InstagramGraphError, match="resultado incerto"):
        publisher.create_reel(queue_id, approved=True)

    attempt = db.instagram_container_attempt(queue_id)
    assert attempt is not None
    assert attempt["status"] == "AMBIGUOUS"
    assert attempt["error_code"] == "TRANSPORT_FAILURE"

    healthy = FakeGraphClient()
    with pytest.raises(ComplianceError, match="exige reconciliacao"):
        InstagramReelPublisher(db, settings, client=healthy).create_reel(
            queue_id,
            approved=True,
        )
    assert healthy.create_calls == 0

    reconciled = publisher.reconcile_container_creation(
        queue_id,
        container_id="18000000000000001",
        confirmed_not_created=False,
        note="Container confirmado no painel Meta.",
    )
    assert reconciled["status"] == "CONTAINER_CREATED"
    assert db.instagram_container_attempt(queue_id) is None
    stored = db.instagram_publication(queue_id)
    assert stored is not None
    assert stored["container_id"] == "18000000000000001"
    assert stored["reconciliation_note"] == "Container confirmado no painel Meta."


def test_confirmed_missing_container_releases_ambiguous_reservation(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    db = Database(settings.database_path)
    db.init()
    queue_id = create_reel_queue(db, settings)
    db.claim_instagram_container_creation(queue_id, ASSET_URL)
    db.mark_instagram_container_creation_ambiguous(queue_id, "TRANSPORT_FAILURE")

    publisher = InstagramReelPublisher(db, settings, client=FakeGraphClient())
    result = publisher.reconcile_container_creation(
        queue_id,
        container_id=None,
        confirmed_not_created=True,
        note="Painel Meta verificado sem container correspondente.",
    )
    assert result == {"queue_id": queue_id, "status": "NOT_CREATED"}
    assert db.instagram_container_attempt(queue_id) is None

    created = publisher.create_reel(queue_id, approved=True)
    assert created["status"] == "CONTAINER_CREATED"


def test_reel_state_machine_stops_after_finished_and_blocks_media_publish(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    db = Database(settings.database_path)
    db.init()
    queue_id = create_reel_queue(db, settings)
    client = FakeGraphClient(status="IN_PROGRESS")
    publisher = InstagramReelPublisher(db, settings, client=client)

    created = publisher.create_reel(queue_id, approved=True)
    assert created["status"] == "CONTAINER_CREATED"
    assert publisher.check_status(queue_id)["status"] == "PROCESSING"
    with pytest.raises(ComplianceError, match="media_publish bloqueado"):
        publisher.publish_reel(queue_id, approved=True)

    client.status = "FINISHED"
    assert publisher.check_status(queue_id)["status"] == "READY_TO_PUBLISH"
    with pytest.raises(ComplianceError, match="rotulo de parceria paga"):
        publisher.publish_reel(queue_id, approved=True)
    assert dict(db.instagram_publication(queue_id))["status"] == "READY_TO_PUBLISH"
    assert db.get_social_queue(queue_id)["status"] == "READY_FOR_PUBLISH"
    assert client.create_calls == 1
    assert client.status_calls == 2
    assert client.publish_calls == 0


def test_real_publish_block_does_not_require_or_leak_token(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    db = Database(settings.database_path)
    db.init()
    queue_id = create_reel_queue(db, settings)
    client = FakeGraphClient()
    publisher = InstagramReelPublisher(db, settings, client=client)
    publisher.create_reel(queue_id, approved=True)
    publisher.check_status(queue_id)
    blocked = replace(settings, instagram_access_token=None, instagram_container_api_enabled=False)
    blocked_publisher = InstagramReelPublisher(db, blocked, client=client)
    with pytest.raises(ComplianceError, match="media_publish bloqueado") as exc_info:
        blocked_publisher.publish_reel(queue_id, approved=True)
    assert "meta-secret-token" not in str(exc_info.value)
    assert dict(db.instagram_publication(queue_id))["status"] == "READY_TO_PUBLISH"
    assert client.publish_calls == 0
    with pytest.raises(ComplianceError, match="confirmacao editorial"):
        publisher.publish_reel(queue_id, approved=False)


def test_dry_run_has_zero_network_and_does_not_persist_attempt(tmp_path: Path) -> None:
    settings = settings_for(tmp_path, dry_run=True)
    db = Database(settings.database_path)
    db.init()
    queue_id = create_reel_queue(db, settings)

    class ExplodingClient:
        def create_reel(self, *args):
            raise AssertionError("DRY_RUN abriu rede")

    publisher = InstagramReelPublisher(db, settings, client=ExplodingClient())
    with pytest.raises(ComplianceError, match="confirmacao editorial"):
        publisher.create_reel(queue_id, approved=False)
    result = publisher.create_reel(queue_id, approved=True)
    assert result == {
        "queue_id": queue_id,
        "status": "SIMULATED",
        "network_calls": 0,
        "media_type": "REELS",
        "asset_url": ASSET_URL,
    }
    assert publisher.publish_reel(queue_id, approved=True) == {
        "queue_id": queue_id,
        "status": "SIMULATED",
        "network_calls": 0,
    }
    assert db.instagram_publication(queue_id) is None
    assert db.instagram_container_attempt(queue_id) is None


@pytest.mark.parametrize(
    ("changes", "message"),
    (
        ({"instagram_container_api_enabled": False}, "INSTAGRAM_REEL_CONTAINER_API_ENABLED"),
        ({"instagram_facebook_login_ready": False}, "conta profissional"),
    ),
)
def test_real_mode_requires_explicit_external_readiness(tmp_path: Path, changes: dict, message: str) -> None:
    settings = settings_for(tmp_path, **changes)
    db = Database(settings.database_path)
    db.init()
    queue_id = create_reel_queue(db, settings)
    with pytest.raises(ComplianceError, match=message):
        InstagramReelPublisher(db, settings, client=FakeGraphClient()).create_reel(
            queue_id,
            approved=True,
        )


def test_only_reel_and_allowlisted_public_asset_are_eligible(tmp_path: Path) -> None:
    settings = settings_for(tmp_path, dry_run=True)
    db = Database(settings.database_path)
    db.init()
    story_id = create_reel_queue(db, settings, format="story")
    publisher = InstagramReelPublisher(db, settings, client=FakeGraphClient())
    with pytest.raises(ComplianceError, match="somente Instagram Reel"):
        publisher.create_reel(story_id, approved=True)

    reel_id = create_reel_queue(db, settings)
    with pytest.raises(TypeError):
        publisher.create_reel(reel_id, "https://media.example/media/other.mp4", approved=True)
    unapproved = replace(settings, instagram_media_base_url="https://unapproved.example/media")
    with pytest.raises(ComplianceError, match="INSTAGRAM_ASSET_ALLOWED_HOSTS"):
        InstagramReelPublisher(db, unapproved, client=FakeGraphClient()).create_reel(reel_id, approved=True)
    signed = replace(settings, instagram_media_base_url="https://media.example/media?token=secret")
    with pytest.raises(ComplianceError, match="query assinada"):
        InstagramReelPublisher(db, signed, client=FakeGraphClient()).create_reel(reel_id, approved=True)


def test_cli_dry_run_reel_create_has_zero_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = settings_for(tmp_path, dry_run=True)
    db = Database(settings.database_path)
    db.init()
    queue_id = create_reel_queue(db, settings)
    monkeypatch.setenv("DATABASE_PATH", str(settings.database_path))
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setenv("PUBLIC_BASE_URL", settings.public_base_url)
    monkeypatch.setenv("CREATIVES_PATH", str(settings.creatives_path))
    monkeypatch.setenv("INSTAGRAM_MEDIA_BASE_URL", str(settings.instagram_media_base_url))
    monkeypatch.setenv("INSTAGRAM_ASSET_ALLOWED_HOSTS", "media.example")
    monkeypatch.setattr(
        "sys.argv",
        ["app.cli", "instagram-reel-create", str(queue_id), "--confirm-reviewed"],
    )

    cli_main()

    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "SIMULATED"
    assert result["network_calls"] == 0
    assert db.instagram_publication(queue_id) is None


def test_cli_real_publish_fails_closed_before_graph_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = settings_for(tmp_path)
    db = Database(settings.database_path)
    db.init()
    queue_id = create_reel_queue(db, settings)
    fake = FakeGraphClient()
    publisher = InstagramReelPublisher(db, settings, client=fake)
    publisher.create_reel(queue_id, approved=True)
    publisher.check_status(queue_id)

    monkeypatch.setenv("DATABASE_PATH", str(settings.database_path))
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("PUBLIC_BASE_URL", settings.public_base_url)
    monkeypatch.setenv("CREATIVES_PATH", str(settings.creatives_path))
    monkeypatch.setenv("INSTAGRAM_MEDIA_BASE_URL", str(settings.instagram_media_base_url))
    monkeypatch.setenv("INSTAGRAM_ASSET_ALLOWED_HOSTS", "media.example")
    monkeypatch.setattr(
        "sys.argv",
        ["app.cli", "instagram-reel-publish", str(queue_id), "--confirm-reviewed"],
    )

    with pytest.raises(ComplianceError, match="media_publish bloqueado"):
        cli_main()

    assert dict(db.instagram_publication(queue_id))["status"] == "READY_TO_PUBLISH"
    assert fake.publish_calls == 0
