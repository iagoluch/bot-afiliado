from __future__ import annotations

import base64
import binascii
import hmac
import html
import json
import re
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from app.config import Settings, validate_public_base_url
from app.db import Database
from app.services.admin_dashboard import PAGES, page_data
from app.services.analytics import DIMENSIONS, analytics_breakdown
from app.services.compliance import ComplianceError, allows_tracking_redirect, distribution_review, validate_offer
from app.services.content import brl
from app.services.content import verified_discount
from app.services.curation import verified_offer_data
from app.services.site import offer_id_from_slug, offer_slug
from app.worker import runtime_status


LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
ADMIN_PREFIXES = ("/admin", "/api")
ADMIN_PATHS = {"/", "/docs", "/redoc", "/openapi.json"}


def _offer_available(offer: dict) -> bool:
    try:
        validate_offer(offer)
    except ComplianceError:
        return False
    return True


def _admin_exposed(settings: Settings) -> bool:
    public = urlsplit(settings.public_base_url)
    return (public.hostname or "").lower() not in LOOPBACK_HOSTS or settings.web_bind_host.lower() not in LOOPBACK_HOSTS


def _validate_admin_configuration(settings: Settings) -> None:
    public = urlsplit(settings.public_base_url)
    if public.username or public.password:
        raise ValueError("PUBLIC_BASE_URL nao pode conter credenciais")
    exposed = _admin_exposed(settings)
    if exposed and not settings.admin_password:
        raise ValueError("ADMIN_PASSWORD e obrigatoria quando o painel pode ser exposto")
    if exposed and public.scheme != "https":
        raise ValueError("PUBLIC_BASE_URL precisa ser HTTPS quando o painel pode ser exposto")


def _authorized(authorization: str | None, password: str) -> bool:
    if not authorization or not authorization.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(authorization[6:], validate=True).decode("utf-8")
        username, candidate = decoded.split(":", 1)
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return False
    return hmac.compare_digest(username, "admin") and hmac.compare_digest(candidate, password)


def _safe_value(value: Any) -> str:
    if value is None:
        return "indisponivel"
    if isinstance(value, (dict, list, tuple)):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return html.escape(str(value), quote=True)


def _table(data: Any) -> str:
    if isinstance(data, dict) and "rows" in data:
        rows = data["rows"]
    elif isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        rows = [{"campo": key, "valor": value} for key, value in data.items()]
    else:
        rows = []
    if not rows:
        return "<p class='empty'>Sem dados reais.</p>"
    columns = list(rows[0])
    head = "".join(f"<th>{_safe_value(column)}</th>" for column in columns)
    body = "".join("<tr>" + "".join(f"<td>{_safe_value(row.get(column))}</td>" for column in columns) + "</tr>" for row in rows)
    return f"<div class='table-wrap'><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>"


def _admin_html(settings: Settings, page: str, data: Any) -> str:
    title = PAGES[page]
    nav = "".join(f"<a href='{'/' if slug == 'overview' else '/admin/' + slug}'>{html.escape(label)}</a>" for slug, label in PAGES.items())
    analytics_controls = ""
    if page == "analytics":
        analytics_controls = "<p>Dimensao: " + " · ".join(
            f"<a href='/admin/analytics?dimension={dimension}'>{dimension}</a>" for dimension in DIMENSIONS
        ) + "</p>"
    heading = "Operacao de afiliados" if page == "overview" else title
    return f"""<!doctype html><html lang='pt-BR'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width'><title>{html.escape(title)}</title><style>
body{{font-family:system-ui;background:#f5f6f8;color:#1f2937;margin:0}}header,main{{max-width:1280px;margin:auto;padding:20px}}nav{{display:flex;gap:12px;flex-wrap:wrap}}nav a{{color:#1d4ed8}}.public{{margin-left:auto}}.mode{{padding:4px 8px;background:#e0e7ff;border-radius:6px}}.table-wrap{{overflow:auto;background:white;border:1px solid #e5e7eb;border-radius:10px}}table{{border-collapse:collapse;width:100%;font-size:.9rem}}th,td{{padding:10px;border-bottom:1px solid #e5e7eb;text-align:left;vertical-align:top;max-width:420px;overflow-wrap:anywhere}}th{{background:#f8fafc}}.empty{{padding:24px;background:white;border-radius:10px;color:#64748b}}</style></head><body><header><nav>{nav}<a class='public' href='/offers'>Hub publico</a></nav></header><main><h1>{html.escape(heading)}</h1><p>Modo <span class='mode'>{'DRY_RUN' if settings.dry_run else 'REAL'}</span></p>{analytics_controls}{_table(data)}</main></body></html>"""


def create_app(app_settings: Settings | None = None, database: Database | None = None) -> FastAPI:
    settings = app_settings or Settings.from_env()
    validate_public_base_url(settings.public_base_url, dry_run=settings.dry_run)
    _validate_admin_configuration(settings)
    db = database or Database(settings.database_path)
    db.init()
    app = FastAPI(title="Motor de Ofertas Afiliadas", version="0.3.0")
    app.mount("/media", StaticFiles(directory=settings.creatives_path, check_dir=False), name="media")

    @app.middleware("http")
    async def protect_admin(request: Request, call_next):
        path = request.url.path
        protected = path in ADMIN_PATHS or any(
            path == prefix or path.startswith(prefix + "/") for prefix in ADMIN_PREFIXES
        )
        if protected and _admin_exposed(settings) and request.url.scheme != "https":
            return Response(status_code=426, content="HTTPS obrigatorio")
        if protected and settings.admin_password and not _authorized(request.headers.get("Authorization"), settings.admin_password):
            return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="BOT AFILIADO admin"'})
        return await call_next(request)

    @app.middleware("http")
    async def add_security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if request.url.path not in {"/docs", "/redoc"}:
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data:; object-src 'none'; base-uri 'none'; "
                "frame-ancestors 'none'; form-action 'self'"
            )
        if not request.url.path.startswith("/media/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/health")
    def health(
        request: Request,
        details: bool = Query(default=False),
        authorization: str | None = Header(default=None),
    ) -> dict:
        basic = {"status": "ok", "dry_run": settings.dry_run}
        if not details:
            return basic
        if _admin_exposed(settings) and request.url.scheme != "https":
            raise HTTPException(status_code=426, detail="HTTPS obrigatorio")
        if settings.admin_password and not _authorized(authorization, settings.admin_password):
            raise HTTPException(
                status_code=401,
                detail="autenticacao administrativa obrigatoria",
                headers={"WWW-Authenticate": 'Basic realm="BOT AFILIADO admin"'},
            )
        return {**basic, "runtime": runtime_status(db, settings)}

    @app.get("/api/overview")
    def overview_api() -> dict:
        return db.overview()

    @app.get("/api/social-queue")
    def social_queue_api() -> list[dict]:
        return [dict(row) for row in db.list_social_queue()]

    @app.get("/api/analytics")
    def analytics_api(dimension: str = Query(default="channel")) -> dict:
        if dimension not in DIMENSIONS:
            raise HTTPException(status_code=400, detail="dimensao invalida")
        return {"dimension": dimension, "rows": analytics_breakdown(db, dimension)}

    @app.get("/api/admin/{page}")
    def admin_api(page: str, dimension: str = Query(default="channel")) -> Any:
        if page not in PAGES:
            raise HTTPException(status_code=404, detail="pagina nao encontrada")
        if dimension not in DIMENSIONS:
            raise HTTPException(status_code=400, detail="dimensao invalida")
        return page_data(db, settings, page, dimension=dimension)

    @app.get("/go/{offer_id}")
    def track_click(
        offer_id: int,
        request: Request,
        channel: str = Query(default="unknown", max_length=50),
        campaign_id: str = Query(default="unknown", max_length=100),
        creative_id: str = Query(default="unknown", max_length=100),
        format: str = Query(default="unknown", max_length=50),
        utm_source: str | None = Query(default=None, max_length=100),
        utm_medium: str | None = Query(default=None, max_length=100),
        utm_campaign: str | None = Query(default=None, max_length=100),
        sub_id: str | None = Query(default=None, max_length=100),
        publication_key: str | None = Query(default=None, max_length=64),
        referer: str | None = Header(default=None),
    ) -> RedirectResponse:
        offer = db.get_offer(offer_id)
        if offer is None:
            raise HTTPException(status_code=404, detail="oferta nao encontrada")
        if not allows_tracking_redirect(dict(offer), settings):
            raise HTTPException(status_code=403, detail="redirect de tracking bloqueado pela regra do programa")
        if not _offer_available(dict(offer)):
            raise HTTPException(status_code=410, detail="oferta indisponivel; consulte dados atualizados")
        affiliate_url = offer["affiliate_url"]
        if publication_key is not None:
            if not re.fullmatch(r"[0-9a-f]{64}", publication_key):
                raise HTTPException(status_code=404, detail="publicacao nao encontrada")
            publication = db.publication_for_tracking_key(
                publication_key, offer_id, channel, campaign_id, creative_id,
            )
            if publication is None:
                raise HTTPException(status_code=404, detail="publicacao nao encontrada")
            affiliate_url = publication["affiliate_url"]
        metadata = json.loads(offer["tracking_metadata_json"] or "{}")
        affiliate_query = parse_qs(urlsplit(affiliate_url).query)
        url_sub_id = next((affiliate_query[key][0] for key in ("sub_id", "subid", "subId") if affiliate_query.get(key)), None)
        official_sub_id = url_sub_id if publication_key is not None else metadata.get("sub_id_from_official_link") or url_sub_id
        db.record_click(
            offer,
            channel=channel,
            campaign_id=campaign_id,
            creative_id=creative_id,
            format=format,
            referrer=referer[:2048] if referer else None,
            utm_source=utm_source,
            utm_medium=utm_medium,
            utm_campaign=utm_campaign,
            sub_id=official_sub_id or sub_id,
        )
        return RedirectResponse(url=affiliate_url, status_code=302)

    @app.get("/", response_class=HTMLResponse)
    def dashboard() -> str:
        return _admin_html(settings, "overview", page_data(db, settings, "overview"))

    @app.get("/admin/{page}", response_class=HTMLResponse)
    def admin_page(page: str, dimension: str = Query(default="channel")) -> str:
        if page not in PAGES:
            raise HTTPException(status_code=404, detail="pagina nao encontrada")
        if dimension not in DIMENSIONS:
            raise HTTPException(status_code=400, detail="dimensao invalida")
        return _admin_html(settings, page, page_data(db, settings, page, dimension=dimension))

    @app.get("/offers", response_class=HTMLResponse)
    def offer_catalog(q: str = Query(default="", max_length=100)) -> str:
        offers = db.search_offers(q, limit=100)
        cards: list[str] = []
        for offer in offers:
            offer_data = dict(offer)
            if distribution_review(offer_data, "site", settings):
                continue
            slug = offer_slug(offer)
            title = html.escape(str(offer["title"]), quote=True)
            merchant = html.escape(str(offer["merchant"]), quote=True)
            coupon = f"<span>Cupom: {html.escape(str(offer['coupon']), quote=True)}</span>" if offer["coupon"] else ""
            if not _offer_available(offer_data):
                cards.append(f"<article><div class='placeholder'>ATUALIZAR</div><small>{merchant}</small><h2>{title}</h2><strong>Preço aguardando atualização</strong></article>")
            else:
                cards.append(f"<article><div class='placeholder'>OFERTA</div><small>{merchant}</small><h2><a href='/o/{slug}'>{title}</a></h2><strong>{html.escape(brl(offer['current_price_cents']))}</strong>{coupon}</article>")
        empty = "<p>Nenhuma oferta encontrada.</p>" if not cards else ""
        query = html.escape(q, quote=True)
        return f"""<!doctype html><html lang='pt-BR'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width'><title>Ofertas</title><style>
body{{font-family:system-ui;background:#f5f6f8;color:#1f2937;margin:0;padding:32px}}main{{max-width:1000px;margin:auto}}form{{display:flex;gap:8px;margin:24px 0}}input{{flex:1;padding:12px}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:16px}}article{{background:white;padding:18px;border-radius:12px}}article span,article small,article strong{{display:block;margin-top:8px}}.placeholder{{height:110px;display:grid;place-items:center;background:#e2e8f0}}</style></head><body><main><h1>Ofertas</h1><p>Conteúdo publicitário. Podemos receber comissão pelas compras feitas nos links.</p><form action='/offers'><input name='q' value='{query}' placeholder='Produto, categoria ou loja'><button>Pesquisar</button></form><section class='grid'>{''.join(cards)}</section>{empty}</main></body></html>"""

    @app.get("/o/{slug}", response_class=HTMLResponse)
    def offer_page(slug: str) -> str:
        offer_id = offer_id_from_slug(slug)
        offer = db.get_offer(offer_id) if offer_id is not None else None
        if offer is None or offer_slug(offer) != slug:
            raise HTTPException(status_code=404, detail="oferta nao encontrada")
        offer_data = verified_offer_data(db, offer_id, offer)
        if distribution_review(offer_data, "site", settings):
            raise HTTPException(status_code=403, detail="oferta aguarda revisao das regras do programa")
        title = html.escape(str(offer["title"]), quote=True)
        description = html.escape(str(offer["description"] or ""), quote=True)
        merchant = html.escape(str(offer["merchant"]), quote=True)
        updated = html.escape(str(offer["updated_at"]), quote=True)
        current_price = html.escape(brl(offer["current_price_cents"]), quote=True)
        verified = verified_discount(offer_data)
        original = f"<p class='original'>De {html.escape(brl(verified[0]))}</p>" if verified else ""
        discount = f"<p>{verified[1]:.0f}% de desconto comprovado</p>" if verified else ""
        coupon = f"<p class='coupon'>Cupom: <strong>{html.escape(str(offer['coupon']), quote=True)}</strong></p>" if offer["coupon"] else ""
        stale = not _offer_available(offer_data)
        if stale:
            click_url = ""
            cta = ""
        elif allows_tracking_redirect(offer_data, settings):
            query = urlencode({"channel": "site", "campaign_id": "hub", "creative_id": "offer-page", "format": "offer_page"})
            click_url = f"/go/{offer['id']}?{query}"
            cta = "Ir para a oferta"
        else:
            click_url = ""
            cta = ""
        action = "<p><strong>Oferta aguardando atualização de preço e estoque.</strong></p>" if stale else (f"<a class='cta' href='{click_url}' rel='nofollow sponsored noopener'>{html.escape(cta)}</a>" if click_url else "<p><strong>Link aguardando revisão do programa.</strong></p>")
        price = "<p class='price'>Preço aguardando atualização</p>" if stale else f"{original}<p class='price'>{current_price}</p>{discount}{coupon}"
        return f"""<!doctype html><html lang='pt-BR'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width'><title>{title}</title><style>
body{{font-family:system-ui;background:#f5f6f8;color:#1f2937;margin:0;padding:32px}}main{{max-width:760px;margin:auto}}article{{background:white;padding:28px;border-radius:16px}}.disclosure{{padding:12px;background:#fef3c7}}.product{{height:240px;display:grid;place-items:center;background:#e2e8f0}}.price{{font-size:2rem;color:#15803d}}.original{{text-decoration:line-through}}.cta{{display:inline-block;padding:14px 22px;background:#166534;color:white;text-decoration:none;border-radius:8px}}</style></head><body><main><p><a href='/offers'>Voltar às ofertas</a></p><article><p class='disclosure'><strong>Publicidade:</strong> podemos receber comissão se você comprar por este link.</p><div class='product'>PRODUTO</div><small>{merchant}</small><h1>{title}</h1><p>{description}</p>{price}<p>Atualizado em {updated}. Preço, estoque e condições podem mudar; confirme na loja.</p>{action}</article></main></body></html>"""

    return app


app = create_app()
