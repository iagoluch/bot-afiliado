from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from app.config import Settings
from app.db import Database
from app.services.compliance import distribution_review, merchant_key, validate_content, validate_offer
from app.services.content import telegram_tracking_url
from app.services.curation import score_offer, verified_offer_data
from app.services.llm import LLMProvider, safe_hook, validated_hook
from app.services.media import CreativeAssets, CreativeGenerator
from app.services.site import offer_slug
from app.services.social_content import ContentSpec, build_content_specs


TIKTOK_POLICY_ACTION = (
    "Revisar elegibilidade e uso com as diretrizes atuais da TikTok Content Posting API; "
    "o rascunho contém texto promocional e não deve ser enviado pela API sem aprovação específica."
)


class P1Pipeline:
    def __init__(
        self, db: Database, settings: Settings, generator: CreativeGenerator | None = None,
        *, provider: LLMProvider | None = None,
    ):
        self.db = db
        self.settings = settings
        self.provider = provider
        self.generator = generator or CreativeGenerator(
            settings.creatives_path,
            ffmpeg_path=settings.ffmpeg_path,
            allowed_image_hosts=settings.awin_allowed_image_hosts,
        )

    def _relative_assets(self, paths: list[Path | str]) -> list[str]:
        relative: list[str] = []
        root = self.settings.creatives_path.resolve()
        for value in paths:
            path = Path(value).resolve()
            try:
                relative.append(path.relative_to(root).as_posix())
            except ValueError as exc:
                raise ValueError("asset gerado fora de CREATIVES_PATH") from exc
        return relative

    @staticmethod
    def _content_key(offer: dict[str, Any], spec: ContentSpec, campaign_id: str) -> str:
        raw = f"p1:{offer['id']}:{offer['current_price_cents']}:{offer.get('coupon') or ''}:{spec.channel}:{spec.format}:{campaign_id}:v1"
        return hashlib.sha256(raw.encode()).hexdigest()

    def _assets_for(self, spec: ContentSpec, assets: CreativeAssets) -> tuple[list[str], str | None]:
        video_detail = None
        if spec.format == "feed":
            paths: list[Path | str] = list(assets.feed)
        elif spec.format == "story":
            paths = [assets.story]
        elif spec.format == "reel":
            paths = [assets.reel_thumbnail, *assets.reel_frames]
            if assets.reel_video.path:
                paths.append(assets.reel_video.path)
            video_detail = assets.reel_video.detail
        elif spec.format == "vertical_video":
            paths = [assets.tiktok_thumbnail, *assets.tiktok_frames]
            if assets.tiktok_video.path:
                paths.append(assets.tiktok_video.path)
            video_detail = assets.tiktok_video.detail
        else:
            paths = []
        return self._relative_assets(paths), video_detail

    def _editorial_hook(
        self, offer: dict[str, Any], reel: ContentSpec, campaign_id: str,
    ) -> tuple[str, str, str | None]:
        # A chave existente preserva o mesmo texto em reexecucoes idempotentes.
        rows = self.db.rows(
            "SELECT payload_json FROM content_packages WHERE content_key=?",
            (self._content_key(offer, reel, campaign_id),),
        )
        if rows:
            try:
                saved = json.loads(rows[0]["payload_json"]).get("editorial_hook", {})
                value = saved.get("text")
                source = saved.get("source")
                if isinstance(value, str) and isinstance(source, str):
                    valid = validated_hook(value, offer=offer)
                    if valid is not None:
                        reason = saved.get("fallback_reason")
                        return valid, source, reason if isinstance(reason, str) else None
            except (ValueError, TypeError, AttributeError):
                pass
        return safe_hook(offer, "instagram_reel", provider=self.provider, settings=self.settings)

    def generate_offer(self, offer_id: int, campaign_id: str = "organic", *, duration_scale: float = 1.0) -> dict[str, Any]:
        row = self.db.get_offer(offer_id)
        if row is None:
            raise ValueError(f"oferta {offer_id} nao encontrada")
        offer = verified_offer_data(self.db, offer_id, row)
        if merchant_key(offer) == "amazon":
            return {
                "offer_id": offer_id,
                "status": "PENDING_MERCHANT_REVIEW",
                "score": None,
                "classification": None,
                "destination_url": None,
                "ffmpeg": {"instagram_reel": "NOT_GENERATED", "tiktok": "NOT_GENERATED"},
                "packages": [],
                "required_action": "PENDING_MERCHANT_REVIEW: aguarda aprovacao escrita e desenho de retencao Amazon",
            }
        # Revalida no momento da geração para não reutilizar cupom expirado,
        # estoque indisponível ou preço inválido importado em execução anterior.
        validate_offer(offer)
        score, classification = score_offer(self.db, offer_id)
        if classification not in {"GOOD", "EXCELLENT"}:
            return {"offer_id": offer_id, "status": "REJECTED_BY_CURATION", "score": score, "classification": classification}

        destination_url = f"{self.settings.public_base_url}/o/{offer_slug(offer)}"
        telegram_url = offer["affiliate_url"] if merchant_key(offer) == "amazon" else telegram_tracking_url(self.settings.public_base_url, offer_id, campaign_id)
        specs = build_content_specs(offer, destination_url, telegram_url=telegram_url)
        reel = next(spec for spec in specs if spec.format == "reel")
        hook, hook_source, fallback_reason = self._editorial_hook(offer, reel, campaign_id)
        editorial_hook = {"text": hook, "source": hook_source, "fallback_reason": fallback_reason}
        if hook_source != "template":
            specs = tuple(
                replace(spec, script=tuple(
                    {**scene, "text": hook} if scene.get("role") == "hook" else scene
                    for scene in spec.script
                )) if spec.format in {"reel", "vertical_video"} else spec
                for spec in specs
            )
        for spec in specs:
            validate_content(spec.caption, allow_http=self.settings.dry_run)
        reel = next(spec for spec in specs if spec.format == "reel")
        tiktok = next(spec for spec in specs if spec.channel == "tiktok")
        assets = self.generator.generate(
            offer,
            campaign_id,
            reel.script,
            tiktok.script,
            duration_scale=duration_scale,
        )
        packages: list[dict[str, Any]] = []
        for spec in specs:
            package_assets, video_detail = self._assets_for(spec, assets)
            payload = spec.payload(destination_url=destination_url)
            if spec.format in {"reel", "vertical_video"}:
                payload["editorial_hook"] = editorial_hook
            if spec.channel == "tiktok":
                payload["distribution"] = "LOCAL_DRAFT_REQUIRES_POLICY_REVIEW"
            content_id = self.db.add_content(
                offer_id,
                spec.channel,
                spec.caption,
                format=spec.format,
                payload=payload,
                assets=package_assets,
                campaign_id=campaign_id,
                content_key=self._content_key(offer, spec, campaign_id),
            )
            queue_status = None
            required_action = None
            queue_id = None
            merchant_review = distribution_review(offer, spec.channel, self.settings) if spec.channel != "site" else None
            if merchant_review:
                queue_status = "PENDING_MERCHANT_REVIEW"
                required_action = merchant_review
                if video_detail:
                    required_action += f" {video_detail}"
                if spec.channel == "tiktok":
                    required_action += f" {TIKTOK_POLICY_ACTION}"
            elif spec.channel == "instagram":
                if spec.format == "reel" and assets.reel_video.status != "READY":
                    queue_status = "ASSET_PENDING"
                    required_action = video_detail or "Gerar o MP4 com FFmpeg antes da revisão editorial."
                elif spec.format == "reel":
                    queue_status = "READY_FOR_PUBLISH"
                    required_action = (
                        "Revisar conteúdo; Graph API pode criar/consultar o container, mas media_publish afiliado "
                        "permanece bloqueado até existir contrato oficial para aplicar o rótulo de parceria paga."
                    )
                else:
                    queue_status = "READY_FOR_PUBLISH"
                    required_action = "Revisar conteúdo e publicar manualmente; API automática não habilitada para este formato."
            elif spec.channel == "tiktok":
                queue_status = "PENDING_POLICY_REVIEW"
                required_action = TIKTOK_POLICY_ACTION
                if assets.tiktok_video.status != "READY":
                    required_action += f" {video_detail or 'O MP4 também depende de FFmpeg.'}"
            if queue_status:
                queue_id = self.db.enqueue_social(
                    offer_id,
                    content_id,
                    spec.channel,
                    spec.format,
                    queue_status,
                    required_action,
                )
            packages.append({
                "content_id": content_id,
                "queue_id": queue_id,
                "channel": spec.channel,
                "format": spec.format,
                "status": queue_status or "GENERATED",
                "assets": package_assets,
                "required_action": required_action,
            })
        return {
            "offer_id": offer_id,
            "status": "GENERATED",
            "score": score,
            "classification": classification,
            "destination_url": destination_url,
            "ffmpeg": {
                "instagram_reel": assets.reel_video.status,
                "tiktok": assets.tiktok_video.status,
            },
            "ab_comparison": self._relative_assets(list(assets.ab_previews)),
            "packages": packages,
        }

    def run(self, offer_ids: list[int], campaign_id: str = "organic", *, duration_scale: float = 1.0) -> dict[str, Any]:
        return {
            "campaign_id": campaign_id,
            "dry_run": self.settings.dry_run,
            "offers": [self.generate_offer(offer_id, campaign_id, duration_scale=duration_scale) for offer_id in offer_ids],
            "external_publications": 0,
        }
