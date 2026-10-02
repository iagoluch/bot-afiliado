from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.config import Settings
from app.db import Database
from app.models import Offer
from app.services.compliance import ComplianceError
from app.services.llm import TemplateProvider
from app.services.media import (
    CreativeGenerator,
    _NoRedirect,
    _approved_image_url,
    _fit_text_block,
    load_approved_product_image,
)
from app.services.p1 import P1Pipeline
from app.services.site import offer_slug
from app.web import create_app


def settings_for(tmp_path: Path, *, ffmpeg_path: str | None = None) -> Settings:
    return Settings(
        database_path=tmp_path / "test.db",
        dry_run=True,
        public_base_url="http://127.0.0.1:8000",
        telegram_bot_token=None,
        telegram_chat_id=None,
        creatives_path=tmp_path / "creatives",
        ffmpeg_path=ffmpeg_path,
    )


def insert_offer(db: Database, **overrides) -> int:
    data = {
        "merchant": "Shopee",
        "affiliate_network": "Shopee Afiliados",
        "external_product_id": "p1-sku",
        "title": "Fone sem fio",
        "description": "Audio e bateria",
        "category": "Eletronicos",
        "original_price_cents": 20000,
        "current_price_cents": 9000,
        "discount_percent": 55,
        "coupon": "CUPOM",
        "coupon_expiration": "2099-12-31T00:00:00Z",
        "shipping": "Frete gratis",
        "rating": 4.9,
        "sales_count": 10000,
        "commission_rate": 15,
        "image_urls": ["https://down-br.img.susercontent.com/product.jpg"],
        "source_url": "https://shopee.com.br/product/1/p1",
        "affiliate_url": "https://s.shopee.com.br/p1?sub_id=original",
        "stock_status": "IN_STOCK",
    }
    data.update(overrides)
    return db.upsert_offer(Offer(**data))


def fake_product(_: str) -> Image.Image:
    return Image.new("RGB", (640, 480), "#38bdf8")


def test_p1_generates_channel_specific_packages_and_safe_queue(tmp_path: Path) -> None:
    settings = settings_for(tmp_path, ffmpeg_path=str(tmp_path / "missing-ffmpeg.exe"))
    db = Database(settings.database_path)
    db.init()
    offer_id = insert_offer(db)
    generator = CreativeGenerator(settings.creatives_path, ffmpeg_path=settings.ffmpeg_path, image_loader=fake_product)
    pipeline = P1Pipeline(db, settings, generator, provider=TemplateProvider())

    first = pipeline.generate_offer(offer_id, "campaign-a")
    second = pipeline.generate_offer(offer_id, "campaign-a")

    assert first["status"] == "GENERATED"
    assert first["ffmpeg"] == {"instagram_reel": "FFMPEG_UNAVAILABLE", "tiktok": "FFMPEG_UNAVAILABLE"}
    assert first["destination_url"].endswith(f"/o/fone-sem-fio-{offer_id}")
    assert len(first["packages"]) == 6
    assert len(first["ab_comparison"]) == 4
    assert all(path.endswith(".png") for path in first["ab_comparison"])
    queue = [dict(row) for row in db.list_social_queue()]
    assert len(queue) == 4
    status_by_format = {row["format"]: row["status"] for row in queue}
    assert status_by_format == {
        "feed": "READY_FOR_PUBLISH",
        "story": "READY_FOR_PUBLISH",
        "reel": "ASSET_PENDING",
        "vertical_video": "PENDING_POLICY_REVIEW",
    }
    tiktok = next(row for row in queue if row["channel"] == "tiktok")
    assert "texto promocional" in tiktok["required_action"]
    payloads = [json.loads(row["payload_json"]) for row in db.rows("SELECT payload_json FROM content_packages ORDER BY id")]
    assert {payload["format"] for payload in payloads} == {"feed", "story", "reel", "vertical_video", "offer_page", "text"}
    assert next(payload for payload in payloads if payload["channel"] == "tiktok")["distribution"] == "LOCAL_DRAFT_REQUIRES_POLICY_REVIEW"
    assert db.rows("SELECT COUNT(*) AS n FROM content_packages")[0]["n"] == 6
    assert second["packages"] == first["packages"]
    telegram = next(package for package in first["packages"] if package["channel"] == "telegram")
    telegram_row = db.rows("SELECT body,payload_json FROM content_packages WHERE id=?", (telegram["content_id"],))[0]
    assert "/go/" in telegram_row["body"]
    assert json.loads(telegram_row["payload_json"])["destination_url"].startswith("http://127.0.0.1:8000/go/")
    assert db.rows("SELECT COUNT(*) AS n FROM publish_queue")[0]["n"] == 0
    assert db.rows("SELECT COUNT(*) AS n FROM publications")[0]["n"] == 0

    feed_path = settings.creatives_path / next(package for package in first["packages"] if package["format"] == "feed")["assets"][0]
    with Image.open(feed_path) as image:
        assert image.size == (1080, 1080)
    digest_before = hashlib.sha256(feed_path.read_bytes()).hexdigest()
    pipeline.generate_offer(offer_id, "campaign-a")
    assert hashlib.sha256(feed_path.read_bytes()).hexdigest() == digest_before


def test_renderer_wraps_long_text_without_clipping_and_removes_scene_debug(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    canvas = Image.new("RGB", (1080, 1920), "white")
    draw = __import__("PIL.ImageDraw", fromlist=["ImageDraw"]).Draw(canvas)
    font, lines = _fit_text_block(
        draw,
        "Conheça este produto com um texto propositalmente longo para validar quebra segura sem clipping lateral",
        max_width=884,
        max_height=300,
        start_size=96,
        min_size=34,
        bold=True,
        max_lines=4,
        spacing=12,
    )
    assert lines
    assert all(
        draw.textbbox((0, 0), line, font=font)[2] - draw.textbbox((0, 0), line, font=font)[0] <= 884
        for line in lines
    )

    captured_secondary: list[str] = []
    captured_video_titles: list[str] = []
    from app.services import media as media_module

    original_card = media_module._card

    def observed_card(*args, **kwargs):
        captured_secondary.append(str(kwargs.get("secondary") or ""))
        if kwargs.get("role") == "hook":
            captured_video_titles.append(str(kwargs.get("title") or ""))
        return original_card(*args, **kwargs)

    monkeypatch.setattr(media_module, "_card", observed_card)
    generator = CreativeGenerator(
        tmp_path / "creatives",
        ffmpeg_path=str(tmp_path / "missing-ffmpeg"),
        image_loader=lambda _url: None,
    )
    offer = {
        "id": 1,
        "title": "Fone Bluetooth Exemplo com título muito maior que o normal",
        "current_price_cents": 11990,
        "original_price_cents": None,
        "discount_percent": None,
        "coupon": "OFERTA10",
        "shipping": "Frete gratis",
        "category": "Eletronicos",
        "image_urls_json": "[]",
    }
    reel_script = (
        {"start": 0, "end": 2, "role": "hook", "text": "Conheça Fone Bluetooth Exemplo com um hook muito longo sem sair da tela"},
        {"start": 2, "end": 6, "role": "product", "text": "Descrição demonstrativa longa para validar o layout vertical"},
        {"start": 6, "end": 10, "role": "price", "text": "Por R$ 119,90"},
        {"start": 10, "end": 14, "role": "benefit", "text": "Confira as condições. Cupom OFERTA10."},
        {"start": 14, "end": 18, "role": "cta", "text": "Confira preço e disponibilidade no link"},
    )
    assets = generator.generate(offer, "visual-v2", reel_script, reel_script)

    assert len(assets.ab_previews) == 4
    assert all(path.exists() for path in assets.ab_previews)
    assert all("Cena " not in secondary for secondary in captured_secondary)
    assert generator._scene_secondary("benefit") == ""
    assert generator._scene_secondary("cta") == ""
    assert generator._scene_secondary("price") == "Preço informado na última atualização"
    assert captured_video_titles
    assert all(title == "" for title in captured_video_titles)
    assert assets.reel_video.status == "FFMPEG_UNAVAILABLE"
    assert assets.tiktok_video.status == "FFMPEG_UNAVAILABLE"

    with Image.open(assets.reel_frames[0]) as frame:
        assert frame.size == (1080, 1920)
    with Image.open(assets.ab_previews[2]) as comparison:
        assert comparison.size == (2160, 1992)


def test_video_plan_adds_zoom_text_entry_and_scene_fades(tmp_path: Path) -> None:
    generator = CreativeGenerator(tmp_path / "creatives", image_loader=fake_product)
    product = fake_product("unused")
    script = (
        {"start": 0, "end": 2, "role": "hook", "text": "Conheça o produto"},
        {"start": 2, "end": 4, "role": "price", "text": "Por R$ 119,90"},
    )
    output = tmp_path / "creatives" / "animation-test"
    output.mkdir(parents=True)

    paths, durations, root = generator._animated_video_plan(
        output=output,
        prefix="reel",
        title="Fone Bluetooth Exemplo",
        product=product,
        script=script,
        duration_scale=1.0,
    )

    assert len(paths) == 18  # 8 keyframes por cena + 2 frames de fade
    assert len(paths) == len(durations)
    assert sum(durations) == pytest.approx(4.0)
    assert any(path.name.startswith("fade-01-") for path in paths)
    assert all(path.exists() for path in paths)

    first = Image.open(paths[0]).convert("RGB")
    last_body = Image.open(paths[5]).convert("RGB")
    try:
        assert first.tobytes() != last_body.tobytes()
    finally:
        first.close()
        last_body.close()

    shutil.rmtree(root)


def test_local_hook_enters_video_package_without_changing_facts_and_is_reused(tmp_path: Path) -> None:
    class Provider:
        calls = 0

        def suggest_hook(self, title: str, category: str, channel: str) -> str:
            self.calls += 1
            return "Conheça os detalhes deste produto"

    settings = settings_for(tmp_path, ffmpeg_path=str(tmp_path / "missing-ffmpeg"))
    db = Database(settings.database_path)
    db.init()
    offer_id = insert_offer(db)
    provider = Provider()
    pipeline = P1Pipeline(
        db, settings,
        CreativeGenerator(settings.creatives_path, ffmpeg_path=settings.ffmpeg_path, image_loader=fake_product),
        provider=provider,
    )

    pipeline.generate_offer(offer_id, "hook-test")
    payload = json.loads(db.rows(
        "SELECT payload_json FROM content_packages WHERE channel='instagram' AND format='reel'"
    )[0]["payload_json"])
    assert payload["editorial_hook"]["text"] == "Conheça os detalhes deste produto"
    assert payload["editorial_hook"]["source"] != "template"
    assert payload["script"][0]["text"] == "Conheça os detalhes deste produto"
    assert "Fone sem fio" in payload["caption"]
    assert "R$ 90,00" in payload["caption"]
    assert "#publi" in payload["caption"]

    pipeline.generate_offer(offer_id, "hook-test")
    assert provider.calls == 1


def test_invalid_local_hook_falls_back_inside_package(tmp_path: Path) -> None:
    class Provider:
        def suggest_hook(self, title: str, category: str, channel: str) -> str:
            return "Só hoje 70% OFF, duas unidades em estoque"

    settings = settings_for(tmp_path, ffmpeg_path=str(tmp_path / "missing-ffmpeg"))
    db = Database(settings.database_path)
    db.init()
    offer_id = insert_offer(db)
    pipeline = P1Pipeline(
        db, settings,
        CreativeGenerator(settings.creatives_path, ffmpeg_path=settings.ffmpeg_path, image_loader=fake_product),
        provider=Provider(),
    )

    pipeline.generate_offer(offer_id, "invalid-hook-test")
    payload = json.loads(db.rows(
        "SELECT payload_json FROM content_packages WHERE channel='instagram' AND format='reel'"
    )[0]["payload_json"])
    assert payload["editorial_hook"]["source"] == "template"
    assert payload["editorial_hook"]["fallback_reason"] == "SUGGESTION_REJECTED"
    assert payload["script"][0]["text"] == "Fone sem fio"
    assert "70%" not in json.dumps(payload)


def test_ffmpeg_uses_the_shared_heavy_work_slot(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    executable = tmp_path / "ffmpeg"
    executable.touch()
    active = False

    @contextmanager
    def locked():
        nonlocal active
        active = True
        try:
            yield
        finally:
            active = False

    def fake_run(*args, **kwargs):
        assert active

    monkeypatch.setattr("app.services.media.heavy_work_slot", locked)
    monkeypatch.setattr("app.services.media.subprocess.run", fake_run)
    result = CreativeGenerator(tmp_path, ffmpeg_path=str(executable))._video(
        (tmp_path / "frame.png",), (1.0,), tmp_path / "video.mp4",
    )
    assert result.status == "READY"
    assert not active


def test_ffmpeg_configured_command_resolves_via_path_without_masking_bad_explicit_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    executable = tmp_path / "ffmpeg"
    executable.touch()
    monkeypatch.setattr(
        "app.services.media.shutil.which",
        lambda command: str(executable) if command == "ffmpeg" else None,
    )

    by_command = CreativeGenerator(tmp_path, ffmpeg_path="ffmpeg")
    assert by_command._ffmpeg_executable() == str(executable)

    explicit_missing = CreativeGenerator(tmp_path, ffmpeg_path=str(tmp_path / "missing" / "ffmpeg"))
    assert explicit_missing._ffmpeg_executable() is None


def test_offer_hub_search_and_page_require_conscious_click_and_escape_html(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    db = Database(settings.database_path)
    db.init()
    offer_id = insert_offer(
        db,
        external_product_id="malicious",
        title='<script>alert("x")</script> Fone',
        description='<img src=x onerror="alert(1)">',
        coupon='"><svg onload=alert(2)>',
        image_urls=["https://evil.example/tracker.png"],
    )
    offer = db.get_offer(offer_id)
    client = TestClient(create_app(settings, db))

    catalog = client.get("/offers", params={"q": "Fone"})
    assert catalog.status_code == 200
    assert "&lt;script&gt;" in catalog.text
    page = client.get(f"/o/{offer_slug(offer)}")
    assert page.status_code == 200
    assert '<script>alert("x")</script>' not in page.text
    assert '<img src=x onerror="alert(1)">' not in page.text
    assert "&lt;svg onload=alert(2)&gt;" in page.text
    assert "evil.example" not in page.text
    assert "Publicidade:" in page.text
    assert "Ir para a oferta" in page.text
    assert "http-equiv='refresh'" not in page.text.lower()
    assert "window.location" not in page.text.lower()
    assert db.rows("SELECT COUNT(*) AS n FROM clicks")[0]["n"] == 0

    click = client.get(f"/go/{offer_id}?channel=site&campaign_id=hub&creative_id=offer-page", follow_redirects=False)
    assert click.status_code == 302
    assert click.headers["location"] == "https://s.shopee.com.br/p1?sub_id=original"
    assert db.rows("SELECT COUNT(*) AS n FROM clicks")[0]["n"] == 1


def test_product_image_loader_accepts_only_approved_https_hosts() -> None:
    assert _approved_image_url("https://down-br.img.susercontent.com/product.jpg")
    assert _approved_image_url("https://cdn.shopee.com.br/product.jpg")
    assert not _approved_image_url("http://down-br.img.susercontent.com/product.jpg")
    assert not _approved_image_url("https://evil.example/product.jpg")
    assert not _approved_image_url("https://shopee.com.br.evil.example/product.jpg")
    assert _NoRedirect().redirect_request(None, None, 302, "Found", {}, "http://127.0.0.1/private") is None


def test_p0_content_table_migrates_to_p1_without_losing_rows(tmp_path: Path) -> None:
    path = tmp_path / "p0.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE content_packages (id INTEGER PRIMARY KEY AUTOINCREMENT, offer_id INTEGER NOT NULL, channel TEXT NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL)")
        connection.execute("INSERT INTO content_packages(offer_id,channel,body,created_at) VALUES(1,'telegram','#publi','2026-09-30T00:00:00Z')")
    db = Database(path)
    db.init()
    columns = {row["name"] for row in db.rows("PRAGMA table_info(content_packages)")}
    assert {"format", "payload_json", "assets_json", "campaign_id", "content_key"} <= columns
    row = db.rows("SELECT channel,body,format FROM content_packages")[0]
    assert dict(row) == {"channel": "telegram", "body": "#publi", "format": "text"}
    assert db.rows("SELECT name FROM sqlite_master WHERE type='table' AND name='social_queue'")


def test_product_image_loader_rejects_oversized_dimensions(monkeypatch: pytest.MonkeyPatch) -> None:
    image = Image.new("RGB", (6001, 1), "white")
    buffer = __import__("io").BytesIO()
    image.save(buffer, format="PNG")

    class Headers:
        @staticmethod
        def get_content_type():
            return "image/png"

        @staticmethod
        def get(name):
            return str(len(buffer.getvalue())) if name == "Content-Length" else None

    class Response:
        headers = Headers()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        @staticmethod
        def read(limit):
            return buffer.getvalue()

    class Opener:
        @staticmethod
        def open(request, timeout):
            return Response()

    monkeypatch.setattr("app.services.media.build_opener", lambda *handlers: Opener())
    assert load_approved_product_image("https://down-br.img.susercontent.com/large.png") is None


def test_p1_revalidates_expired_coupon_before_rendering(tmp_path: Path) -> None:
    settings = settings_for(tmp_path, ffmpeg_path=str(tmp_path / "missing.exe"))
    db = Database(settings.database_path)
    db.init()
    offer_id = insert_offer(db)
    with db.connect() as connection:
        connection.execute("UPDATE offers SET coupon_expiration='2000-01-01T00:00:00Z' WHERE id=?", (offer_id,))
    generator = CreativeGenerator(settings.creatives_path, ffmpeg_path=settings.ffmpeg_path, image_loader=fake_product)
    with pytest.raises(ComplianceError, match="cupom expirado"):
        P1Pipeline(db, settings, generator, provider=TemplateProvider()).generate_offer(offer_id)


def test_p1_real_mode_requires_https_public_url_before_rendering(tmp_path: Path) -> None:
    settings = replace(settings_for(tmp_path), dry_run=False)
    db = Database(settings.database_path)
    db.init()
    generator = CreativeGenerator(
        settings.creatives_path,
        ffmpeg_path=str(tmp_path / "missing.exe"),
        image_loader=fake_product,
    )
    with pytest.raises(ValueError, match="HTTPS"):
        P1Pipeline(db, settings, generator, provider=TemplateProvider())
    assert not settings.creatives_path.exists()


def _development_ffmpeg() -> str | None:
    executable = shutil.which("ffmpeg")
    if executable:
        return executable
    try:
        import imageio_ffmpeg
    except ImportError:
        return None
    candidate = imageio_ffmpeg.get_ffmpeg_exe()
    return candidate if Path(candidate).is_file() else None


@pytest.mark.skipif(_development_ffmpeg() is None, reason="FFmpeg nao esta disponivel neste ambiente")
def test_ffmpeg_renders_real_short_vertical_mp4(tmp_path: Path) -> None:
    ffmpeg = _development_ffmpeg()
    settings = settings_for(tmp_path, ffmpeg_path=ffmpeg)
    db = Database(settings.database_path)
    db.init()
    offer_id = insert_offer(db)
    generator = CreativeGenerator(settings.creatives_path, ffmpeg_path=ffmpeg, image_loader=fake_product)

    result = P1Pipeline(db, settings, generator, provider=TemplateProvider()).generate_offer(
        offer_id, "ffmpeg-test", duration_scale=0.01,
    )

    assert result["ffmpeg"] == {"instagram_reel": "READY", "tiktok": "READY"}
    reel = next(package for package in result["packages"] if package["format"] == "reel")
    mp4 = settings.creatives_path / next(path for path in reel["assets"] if path.endswith(".mp4"))
    assert mp4.read_bytes()[4:8] == b"ftyp"
    assert mp4.stat().st_size > 1_000
