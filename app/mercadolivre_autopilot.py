"""Autopilot ML via visible marketplace and official affiliate generator UI.

No private API, cookies extraction or CAPTCHA bypass. Browser sessions stay local.
Single-product generator flow ensures source-to-affiliate matching is unambiguous.
Never queue offers unless product details, price, stock button and generated
affiliate link have all been observed in the current browser session.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

from app.config import Settings
from app.db import Database
from app.models import Offer, money_to_cents, utc_now
from app.services.collection import is_technology_offer

PROFILE = Path("data/ml_browser_profile")
CONFIG = Path("data/ml_autopilot_config.json")
KEYWORDS = (
    "ssd nvme", "fone bluetooth", "memoria ram ddr4",
    "mouse gamer", "carregador usb c", "monitor gamer", "notebook",
    "placa de video", "tablet android", "teclado mecanico",
)
ML_HOSTS = {"mercadolivre.com.br", "www.mercadolivre.com.br", "produto.mercadolivre.com.br"}
CATALOG_PATH = re.compile(r"(?:^|/)p/MLB[0-9]{5,16}(?:/|$)", re.I)
ITEM_PATH = re.compile(r"MLB-[0-9]{5,16}", re.I)
AFFILIATE_CODE = re.compile(r"https://meli\.la/[A-Za-z0-9]{3,80}(?![A-Za-z0-9])|https://(?:www\.)?mercadolivre\.com/sec/[A-Za-z0-9_-]{3,100}", re.I)


class MLError(ValueError):
    pass


def _host_ok(url: str, *, generator: bool = False) -> bool:
    try:
        parsed = urlsplit(url)
        return (
            parsed.scheme == "https"
            and (parsed.hostname or "").lower() in ML_HOSTS
            and parsed.username is None and parsed.password is None
            and parsed.port in (None, 443)
            and bool(parsed.path.strip("/"))
            and len(url) < 2048
        )
    except ValueError:
        return False


def normalize_product_url(url: str) -> str | None:
    if not _host_ok(url):
        return None
    parsed = urlsplit(url)
    if not (CATALOG_PATH.search(parsed.path) or ITEM_PATH.search(parsed.path)):
        return None
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def affiliate_from_text(value: str) -> str | None:
    if not isinstance(value, str) or len(value) > 20000:
        return None
    found = AFFILIATE_CODE.findall(value)
    # One product per generator run. Never map ambiguous multiple links.
    unique = list(dict.fromkeys(found))
    return unique[0] if len(unique) == 1 else None


def _money_from_visible(text: str) -> int | None:
    digits = re.fullmatch(r"[0-9]{1,7}(?:\.[0-9]{3})*(?:,[0-9]{2})?", text.strip())
    if not digits:
        return None
    return money_to_cents(text)


def _load_config() -> str:
    if not CONFIG.is_file():
        raise MLError("SETUP_NECESSARIO: execute python -m app.mercadolivre_autopilot setup")
    try:
        data = json.loads(CONFIG.read_text(encoding="utf-8"))
        url = data["generator_url"]
    except (OSError, ValueError, KeyError, TypeError):
        raise MLError("CONFIGURACAO_DO_GERADOR_INVALIDA") from None
    if not isinstance(url, str) or not _host_ok(url):
        raise MLError("URL_DO_GERADOR_INVALIDA")
    return url


def _save_config(url: str) -> None:
    if not _host_ok(url):
        raise MLError("URL_DO_GERADOR_INVALIDA")
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG.with_suffix(".tmp")
    tmp.write_text(json.dumps({"generator_url": url}, ensure_ascii=False), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(CONFIG)


def _first_text(locator, *, timeout: int = 3000) -> str:
    try:
        return locator.first.inner_text(timeout=timeout).strip()
    except Exception:
        return ""


def _find_search_candidates(page, settings: Settings, keyword: str, limit: int = 6) -> list[dict]:
    """Read only public-facing search cards, not API payloads or private endpoints."""
    if not re.fullmatch(r"[\w ]{2,45}", keyword):
        raise MLError("TERMO_DE_BUSCA_INVALIDO")
    page.goto(f"https://lista.mercadolivre.com.br/{quote(keyword.replace(' ', '-'))}", wait_until="domcontentloaded", timeout=25000)
    if not _host_ok(page.url):
        # lista.mercadolivre.com.br is deliberately allowed only as a search
        # origin, never as a source link or affiliate generator.
        if (urlsplit(page.url).hostname or "") != "lista.mercadolivre.com.br":
            raise MLError("BUSCA_REDIRECIONOU_PARA_SITE_INESPERADO")
    cards = page.locator("li.ui-search-layout__item, div.poly-card")
    candidates = []
    seen = set()
    for i in range(min(cards.count(), 18)):
        card = cards.nth(i)
        anchor = card.locator("a.poly-component__title, a.ui-search-item__group__element, a.poly-card__title").first
        try:
            href = anchor.get_attribute("href", timeout=2000) or ""
            title = anchor.inner_text(timeout=2000).strip()
        except Exception:
            continue
        source = normalize_product_url(href)
        if not source or source in seen or len(title) < 6:
            continue
        if not is_technology_offer({"title": title, "category": "", "brand": ""}, terms=settings.technology_offer_terms):
            continue
        seen.add(source)
        candidates.append({"url": source, "title": title})
        if len(candidates) >= limit:
            break
    return candidates


def _visible_price(page) -> int | None:
    """Use product-page normal price display only; never infer a crossed-out price."""
    for selector in (
        ".ui-pdp-price__main-container .andes-money-amount",
        "[data-testid='price-part'] .andes-money-amount",
        ".ui-pdp-price__second-line .andes-money-amount",
    ):
        loc = page.locator(selector).first
        try:
            if not loc.is_visible(timeout=1500):
                continue
            whole = _first_text(loc.locator(".andes-money-amount__fraction"))
            cents = _first_text(loc.locator(".andes-money-amount__cents"))
            if whole and (not cents or re.fullmatch(r"\d{1,2}", cents)):
                value = whole + ("," + cents.zfill(2) if cents else "")
                price = _money_from_visible(value)
                if price and 100 <= price <= 50000000:
                    return price
        except Exception:
            continue
    return None


def _validate_product_page(page, settings: Settings, candidate: dict) -> dict | None:
    page.goto(candidate["url"], wait_until="domcontentloaded", timeout=25000)
    if not _host_ok(page.url):
        return None
    title = _first_text(page.locator("h1.ui-pdp-title, h1.poly-component__title"))
    if not title or not is_technology_offer(
        {"title": title, "category": "", "brand": ""}, terms=settings.technology_offer_terms
    ):
        return None
    price = _visible_price(page)
    if price is None:
        return None
    # Only an explicit active purchase control is acceptable stock evidence.
    buy = page.get_by_role("button", name=re.compile(r"^Comprar agora$", re.I))
    try:
        available = buy.count() > 0 and buy.first.is_visible(timeout=1500) and buy.first.is_enabled(timeout=1500)
    except Exception:
        available = False
    if not available:
        return None
    source = normalize_product_url(page.url)
    if not source:
        return None
    ref = re.search(r"MLB[0-9]{5,16}", source, re.I)
    item_id = ref.group(0).upper() if ref else None
    if not item_id:
        return None
    return {
        "external_id": item_id,
        "title": title[:250],
        "source_url": source,
        "price_cents": price,
        "stock_status": "IN_STOCK",
        "confirmed_at": utc_now(),
    }


def _has_generator(page) -> bool:
    try:
        return page.locator("textarea").count() >= 1 and page.get_by_role("button", name=re.compile(r"^Gerar$", re.I)).count() >= 1
    except Exception:
        return False


def _generate_affiliate_link(page, generator_url: str, source_url: str) -> str | None:
    """Use only visible official generator UI, never copy authentication state."""
    page.goto(generator_url, wait_until="domcontentloaded", timeout=30000)
    if not _host_ok(page.url) or not _has_generator(page):
        raise MLError("GERADOR_NAO_ENCONTRADO_OU_LOGIN_EXPIRADO")
    page.locator("textarea").first.fill(source_url)
    # User-selected 'telegram' label is preserved, never silently replaced.
    page.get_by_role("button", name=re.compile(r"^Gerar$", re.I)).first.click(timeout=10000)
    page.wait_for_timeout(1800)
    try:
        page.get_by_text(re.compile(r"https://meli\.la/|mercadolivre\.com/sec/", re.I)).first.wait_for(timeout=7000)
    except Exception:
        pass
    # Read visible text and same-origin DOM links without clipboard/cookie access.
    links = page.locator("a[href*='meli.la/'], a[href*='mercadolivre.com/sec/']")
    hrefs = []
    for i in range(min(links.count(), 6)):
        try:
            hrefs.append(links.nth(i).get_attribute("href") or "")
        except Exception:
            pass
    short = affiliate_from_text("\n".join(hrefs))
    if short:
        return short
    # Some versions render generated links in input fields.
    for loc in (page.locator("input[readonly]"), page.locator("textarea[readonly]")):
        for i in range(min(loc.count(), 6)):
            try:
                hrefs.append(loc.nth(i).input_value())
            except Exception:
                pass
    return affiliate_from_text("\n".join(hrefs))


def _should_queue(settings: Settings) -> bool:
    # Dedicated CLI may only queue when the existing real Telegram gates hold.
    return settings.telegram_only_mode and (settings.dry_run or settings.telegram_real_enabled)


def _queue_validated_product(db: Database, settings: Settings, details: dict, affiliate: str) -> dict:
    if not _should_queue(settings) or affiliate_from_text(affiliate) != affiliate:
        raise MLError("PUBLICACAO_OU_LINK_NAO_AUTORIZADO")
    if not is_technology_offer({"title": details["title"], "category": "", "brand": ""}, terms=settings.technology_offer_terms):
        raise MLError("PRODUTO_FORA_DO_NICHO")
    timestamp = datetime.fromisoformat(details["confirmed_at"].replace("Z", "+00:00"))
    if (datetime.now(timezone.utc) - timestamp).total_seconds() > 300:
        raise MLError("VALIDACAO_DE_PRECO_EXPIRADA")
    from app.services.pipeline import Pipeline
    offer = Offer(
        merchant="Mercado Livre",
        affiliate_network="Mercado Livre Afiliados e Criadores",
        external_product_id=details["external_id"],
        title=details["title"],
        category="tecnologia",
        current_price_cents=details["price_cents"],
        source_url=details["source_url"],
        affiliate_url=affiliate,
        deeplink=affiliate,
        collected_at=details["confirmed_at"],
        expires_at=(timestamp + timedelta(minutes=15)).isoformat(timespec="seconds"),
        stock_status="IN_STOCK",
        source_type="ML_BROWSER_VERIFIED",
        tracking_metadata={"browser_verified": True, "affiliate_ui_generated": True},
    )
    pipeline = Pipeline(db, settings)
    ids = pipeline.ingest_offers([offer], content_campaign_id="ml-browser-official")
    return {"status": "QUEUED_FOR_WORKER", "offer_ids": ids, "published_now": 0, "affiliate_url": affiliate}


def run_once(settings: Settings, generator_url: str, *, keyword: str | None = None) -> dict:
    if not _should_queue(settings):
        raise MLError("MODO_TELEGRAM_ONLY_NAO_CONFIGURADO")
    from playwright.sync_api import sync_playwright
    PROFILE.mkdir(parents=True, exist_ok=True)
    os.chmod(PROFILE, 0o700)
    db = Database(settings.database_path)
    db.init()
    if keyword is None:
        # Pick a different tech group each 6-hour interval.
        slot = int(datetime.now(timezone.utc).timestamp()) // (6 * 3600)
        keyword = KEYWORDS[slot % len(KEYWORDS)]
    collected = 0
    skipped = 0
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            str(PROFILE), headless=False, viewport={"width": 1200, "height": 800},
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            candidates = _find_search_candidates(page, settings, keyword, limit=6)
            for candidate in candidates:
                if collected >= 1:
                    break
                # Never repeat an offer already published within 24h.
                item_match = re.search(r"MLB[0-9]{5,16}", candidate["url"], re.I)
                if item_match:
                    already = db.rows(
                        """SELECT 1 FROM publications p JOIN offers o ON p.offer_id=o.id
                        WHERE o.external_product_id=? AND p.channel='telegram'
                        AND p.dry_run=0 AND p.published_at>=? LIMIT 1""",
                        (item_match.group(0).upper(), (datetime.now(timezone.utc)-timedelta(hours=24)).isoformat()),
                    )
                    if already:
                        skipped += 1
                        continue
                details = _validate_product_page(page, settings, candidate)
                if not details:
                    skipped += 1
                    continue
                affiliate = _generate_affiliate_link(page, generator_url, details["source_url"])
                if not affiliate:
                    skipped += 1
                    continue
                _queue_validated_product(db, settings, details, affiliate)
                collected += 1
            return {"status": "QUEUED" if collected else "NO_VERIFIED_OFFER",
                    "keyword": keyword, "queued": collected, "skipped": skipped,
                    "telegram_delivery": "worker"}
        finally:
            context.close()


def setup() -> None:
    from playwright.sync_api import sync_playwright
    PROFILE.mkdir(parents=True, exist_ok=True)
    os.chmod(PROFILE, 0o700)
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(PROFILE), headless=False, viewport={"width": 1200, "height": 800}
        )
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto("https://www.mercadolivre.com.br/afiliados", wait_until="domcontentloaded")
            print("Abra o Gerador de produtos recomendados e confira a etiqueta 'telegram'.")
            input("Quando estiver na tela do Gerador (como no print), pressione ENTER aqui: ")
            for candidate_page in ctx.pages:
                if _host_ok(candidate_page.url) and _has_generator(candidate_page):
                    _save_config(candidate_page.url)
                    print(json.dumps({"status": "GERADOR_CONFIGURADO", "url": candidate_page.url}))
                    return
            raise MLError("GERADOR_NAO_DETECTADO: abra a tela com textarea e botao Gerar")
        finally:
            ctx.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Autopiloto Mercado Livre via navegador e Gerador oficial")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("setup")
    once = sub.add_parser("once")
    once.add_argument("--keyword", default=None)
    args = parser.parse_args()
    try:
        if args.command == "setup":
            setup()
        else:
            report = run_once(Settings.from_env(), _load_config(), keyword=args.keyword)
            print(json.dumps(report, ensure_ascii=False, indent=2))
    except (MLError, ImportError) as exc:
        print(json.dumps({"status":"BLOCKED","reason":str(exc) if isinstance(exc, MLError) else "PLAYWRIGHT_NAO_INSTALADO"}),file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
