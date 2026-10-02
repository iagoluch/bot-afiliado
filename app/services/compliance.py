from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlsplit

from app.config import Settings
from app.models import normalize_utc_timestamp


class ComplianceError(ValueError):
    pass


DEFAULT_MERCHANT_RULES = {
    "shopee": {"require_public": True, "blocked_channels": ()},
    "mercado livre": {"require_public": True, "blocked_channels": ("private_group", "paid_search", "offline")},
    "amazon": {"require_public": True, "blocked_channels": (), "merchant_review": True, "allow_redirect": False},
    "awin": {"require_public": True, "blocked_channels": ()},
    "admitad": {"require_public": True, "blocked_channels": (), "merchant_review": True, "allow_redirect": False},
    "magalu": {"require_public": True, "blocked_channels": (), "merchant_review": True, "allow_redirect": False},
    "aliexpress": {"require_public": True, "blocked_channels": (), "merchant_review": True, "allow_redirect": False},
    "shein": {"require_public": True, "blocked_channels": (), "merchant_review": True, "allow_redirect": False},
}


def merchant_key(offer: dict) -> str:
    merchant = str(offer.get("merchant") or "").lower()
    for key in ("amazon", "magalu", "aliexpress", "shein", "mercado livre"):
        if key in merchant:
            return key
    value = f"{merchant} {offer.get('affiliate_network','')}".lower()
    return next((key for key in DEFAULT_MERCHANT_RULES if key in value), "")


def merchant_rule(offer: dict, settings: Settings) -> dict:
    key = merchant_key(offer)
    baseline = DEFAULT_MERCHANT_RULES.get(key, {"require_public": True, "blocked_channels": ()})
    rule = dict(baseline)
    overrides = settings.merchant_channel_rules or {}
    rule.update(overrides.get(key, {}))
    if key == "amazon":
        rule.update({"require_public": True, "merchant_review": True, "allow_redirect": False})
    elif key == "mercado livre":
        configured = {str(item).lower() for item in rule.get("blocked_channels", ())}
        rule["blocked_channels"] = tuple(configured | set(baseline["blocked_channels"]))
        rule["require_public"] = True
    return rule


def distribution_review(offer: dict, channel: str, settings: Settings) -> str | None:
    rule = merchant_rule(offer, settings)
    normalized = channel.strip().lower()
    blocked = {str(item).lower() for item in rule.get("blocked_channels", ())}
    if normalized in blocked:
        return f"canal {normalized} bloqueado pela regra do programa"
    visibility_map = {"site": "public", "telegram": "public", "instagram": "public", "tiktok": "public"}
    visibility_map.update(settings.channel_visibility or {})
    visibility = str(visibility_map.get(normalized, "unknown")).lower()
    if rule.get("require_public") and visibility != "public":
        return f"canal {normalized} nao esta configurado como publico"
    if rule.get("merchant_review"):
        return "PENDING_MERCHANT_REVIEW: termos do programa exigem revisão do canal e do link"
    return None


def validate_distribution(offer: dict, channel: str, settings: Settings) -> None:
    reason = distribution_review(offer, channel, settings)
    if reason:
        raise ComplianceError(reason)


def allows_tracking_redirect(offer: dict, settings: Settings) -> bool:
    rule = merchant_rule(offer, settings)
    return not rule.get("merchant_review", False) and bool(rule.get("allow_redirect", True))


def is_offer_stale(offer: dict, *, now: datetime | None = None) -> bool:
    expiration = offer.get("expires_at")
    if not expiration:
        return False
    try:
        expires = datetime.fromisoformat(str(expiration).replace("Z", "+00:00"))
    except ValueError:
        return True
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    return expires <= (now or datetime.now(timezone.utc))


def validate_offer(offer: dict) -> None:
    errors: list[str] = []

    for field in ("merchant", "affiliate_network", "external_product_id", "title"):
        if not str(offer.get(field) or "").strip():
            errors.append(f"{field} ausente")

    for field in ("source_url", "affiliate_url"):
        value = str(offer.get(field, "")).strip()
        try:
            parsed = urlsplit(value)
            valid = (
                parsed.scheme == "https"
                and bool(parsed.hostname)
                and parsed.username is None
                and parsed.password is None
                and not any(char.isspace() or ord(char) < 32 for char in value)
            )
        except ValueError:
            valid = False
        if not valid:
            errors.append(f"{field} precisa ser URL HTTPS")

    try:
        current = int(offer.get("current_price_cents") or 0)
    except (TypeError, ValueError):
        current = 0
    if current <= 0:
        errors.append("preco atual precisa ser positivo")

    original = offer.get("original_price_cents")
    if original is not None:
        try:
            original_value = int(original)
        except (TypeError, ValueError):
            errors.append("preco anterior invalido")
        else:
            if original_value <= 0:
                errors.append("preco anterior precisa ser positivo")
            elif current > 0 and original_value < current:
                errors.append("preco anterior menor que preco atual")

    stock_status = str(offer.get("stock_status") or "UNKNOWN").strip().upper()
    if stock_status not in {"IN_STOCK", "OUT_OF_STOCK", "UNKNOWN"}:
        errors.append("status de estoque invalido")
    elif stock_status == "OUT_OF_STOCK":
        errors.append("produto indisponivel")

    numeric_ranges = (
        ("rating", 0.0, 5.0, "rating"),
        ("sales_count", 0.0, None, "sales_count"),
        ("commission_rate", 0.0, 100.0, "commission_rate"),
        ("commission_estimate_cents", 0.0, None, "commission_estimate_cents"),
        ("discount_percent", 0.0, 100.0, "discount_percent"),
    )
    for field, minimum, maximum, label in numeric_ranges:
        value = offer.get(field)
        if value is None:
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            errors.append(f"{label} invalido")
            continue
        if number < minimum or (maximum is not None and number > maximum):
            errors.append(f"{label} fora do intervalo permitido")

    collected_at = offer.get("collected_at")
    if collected_at:
        try:
            normalize_utc_timestamp(str(collected_at), field="collected_at")
        except ValueError as exc:
            errors.append(str(exc))

    expiration = offer.get("coupon_expiration")
    if offer.get("coupon") and expiration:
        try:
            expires = datetime.fromisoformat(str(expiration).replace("Z", "+00:00"))
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if expires < datetime.now(timezone.utc):
                errors.append("cupom expirado")
        except ValueError:
            errors.append("data de expiracao do cupom invalida")
    if is_offer_stale(offer):
        errors.append("oferta expirada; atualize preco, estoque e condicoes")
    if errors:
        raise ComplianceError("; ".join(errors))

def validate_content(
    body: str,
    *,
    allow_http: bool = False,
    max_chars: int | None = None,
) -> None:
    if max_chars is not None and not 1 <= len(body) <= max_chars:
        raise ComplianceError(f"conteudo excede limite de {max_chars} caracteres")
    lowered = body.lower()
    if "#publi" not in lowered and "#publicidade" not in lowered:
        raise ComplianceError("identificacao publicitaria ausente")
    if "http://" in lowered and not allow_http:
        raise ComplianceError("conteudo contem link HTTP inseguro")
