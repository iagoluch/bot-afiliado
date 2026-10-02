from __future__ import annotations

import ipaddress
import json
import re
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from app.config import Settings
from app.db import Database
from app.services.compliance import ComplianceError, validate_content, validate_distribution, validate_offer


GRAPH_ORIGIN = "https://graph.facebook.com"
MAX_RESPONSE_BYTES = 64 * 1024
ID_PATTERN = re.compile(r"^[0-9]+$")
VERSION_PATTERN = re.compile(r"^v[0-9]+\.[0-9]+$")


class InstagramGraphError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "INSTAGRAM_GRAPH_ERROR",
        ambiguous: bool = False,
    ):
        super().__init__(message)
        self.code = code
        self.ambiguous = ambiguous


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def _default_open(request: Request, timeout: float):
    return build_opener(_NoRedirectHandler()).open(request, timeout=timeout)


class InstagramGraphClient:
    """Fixed-origin client limited to Reel container creation and status."""

    def __init__(
        self,
        access_token: str,
        api_version: str,
        *,
        timeout: float = 20.0,
        opener: Callable[[Request, float], Any] = _default_open,
    ):
        if not access_token:
            raise InstagramGraphError("INSTAGRAM_ACCESS_TOKEN ausente", code="MISSING_TOKEN")
        if not VERSION_PATTERN.fullmatch(api_version):
            raise InstagramGraphError(
                "INSTAGRAM_GRAPH_API_VERSION deve usar o formato vNN.N",
                code="INVALID_API_VERSION",
            )
        self._access_token = access_token
        self.api_version = api_version
        self.timeout = timeout
        self._opener = opener

    @staticmethod
    def _identifier(value: str, label: str) -> str:
        if not ID_PATTERN.fullmatch(value):
            raise InstagramGraphError(f"{label} invalido", code="INVALID_IDENTIFIER")
        return value

    @staticmethod
    def _read_json(response) -> dict[str, Any]:  # noqa: ANN001
        raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise InstagramGraphError("resposta da Instagram Graph API excedeu o limite", code="RESPONSE_TOO_LARGE")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise InstagramGraphError("resposta invalida da Instagram Graph API", code="INVALID_RESPONSE") from exc
        if not isinstance(payload, dict):
            raise InstagramGraphError("resposta invalida da Instagram Graph API", code="INVALID_RESPONSE")
        return payload

    @staticmethod
    def _graph_error(
        payload: dict[str, Any],
        *,
        http_status: int | None = None,
        ambiguous: bool = False,
    ) -> InstagramGraphError:
        error = payload.get("error") if isinstance(payload, dict) else None
        raw_code = str(error.get("code", "")) if isinstance(error, dict) else ""
        raw_subcode = str(error.get("error_subcode", "")) if isinstance(error, dict) else ""
        graph_code = raw_code if raw_code.isdigit() else "UNKNOWN"
        subcode = raw_subcode if raw_subcode.isdigit() else ""
        code = f"GRAPH_{graph_code}" + (f"_{subcode}" if subcode else "")
        status = f" HTTP {http_status}" if http_status is not None else ""
        return InstagramGraphError(
            f"Instagram Graph API recusou a requisicao ({code}{status})",
            code=code,
            ambiguous=ambiguous,
        )

    def _request(self, method: str, path: str, parameters: dict[str, str]) -> dict[str, Any]:
        url = f"{GRAPH_ORIGIN}/{self.api_version}/{path.lstrip('/')}"
        data = None
        if method == "GET":
            if parameters:
                url = f"{url}?{urlencode(parameters)}"
        else:
            data = urlencode(parameters).encode("utf-8")
        request = Request(
            url,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self._access_token}",
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "BotAfiliado/1.0",
            },
        )
        try:
            with self._opener(request, self.timeout) as response:
                try:
                    payload = self._read_json(response)
                except InstagramGraphError as exc:
                    if method != "GET":
                        raise InstagramGraphError(
                            str(exc),
                            code=exc.code,
                            ambiguous=True,
                        ) from exc
                    raise
        except HTTPError as exc:
            try:
                payload = self._read_json(exc)
            except InstagramGraphError:
                payload = {}
            ambiguous = method != "GET" and (exc.code >= 500 or exc.code in {408, 425})
            raise self._graph_error(
                payload,
                http_status=exc.code,
                ambiguous=ambiguous,
            ) from None
        except (URLError, TimeoutError, OSError):
            raise InstagramGraphError(
                "falha de transporte da Instagram Graph API",
                code="TRANSPORT_FAILURE",
                ambiguous=method != "GET",
            ) from None
        if "error" in payload:
            raise self._graph_error(payload)
        return payload

    def create_reel(self, ig_user_id: str, video_url: str, caption: str) -> str:
        account_id = self._identifier(ig_user_id, "INSTAGRAM_USER_ID")
        payload = self._request(
            "POST",
            f"{account_id}/media",
            {
                "media_type": "REELS",
                "video_url": video_url,
                "caption": caption,
                "share_to_feed": "true",
            },
        )
        container_id = str(payload.get("id", ""))
        try:
            return self._identifier(container_id, "container_id")
        except InstagramGraphError as exc:
            raise InstagramGraphError(
                str(exc),
                code=exc.code,
                ambiguous=True,
            ) from exc

    def container_status(self, container_id: str) -> str:
        value = self._identifier(container_id, "container_id")
        payload = self._request("GET", value, {"fields": "status_code,status"})
        status = str(payload.get("status_code", "")).upper()
        if status not in {"IN_PROGRESS", "FINISHED", "ERROR", "EXPIRED", "PUBLISHED"}:
            raise InstagramGraphError("status de container desconhecido", code="UNKNOWN_CONTAINER_STATUS")
        return status

class InstagramReelPublisher:
    def __init__(
        self,
        db: Database,
        settings: Settings,
        *,
        client: InstagramGraphClient | None = None,
    ):
        self.db = db
        self.settings = settings
        self._provided_client = client

    def _client(self) -> InstagramGraphClient:
        if self._provided_client is not None:
            return self._provided_client
        return InstagramGraphClient(
            self.settings.instagram_access_token or "",
            self.settings.instagram_graph_api_version or "",
        )

    def _real_configuration(self) -> str:
        if not self.settings.instagram_container_api_enabled:
            raise ComplianceError("INSTAGRAM_REEL_CONTAINER_API_ENABLED precisa ser true")
        if not self.settings.instagram_facebook_login_ready:
            raise ComplianceError(
                "confirme conta profissional, Page vinculada e instagram_content_publish em INSTAGRAM_FACEBOOK_LOGIN_READY"
            )
        user_id = self.settings.instagram_user_id or ""
        if not ID_PATTERN.fullmatch(user_id):
            raise ComplianceError("INSTAGRAM_USER_ID ausente ou invalido")
        if not self.settings.instagram_access_token:
            raise ComplianceError("INSTAGRAM_ACCESS_TOKEN ausente")
        if not VERSION_PATTERN.fullmatch(self.settings.instagram_graph_api_version or ""):
            raise ComplianceError("INSTAGRAM_GRAPH_API_VERSION ausente ou invalida")
        return user_id

    def _public_asset_url(self, relative_asset: str) -> str:
        base_url = self.settings.instagram_media_base_url or ""
        parsed = urlsplit(base_url)
        hostname = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not hostname or parsed.username or parsed.password or parsed.fragment:
            raise ComplianceError("INSTAGRAM_MEDIA_BASE_URL precisa ser HTTPS publica sem credencial ou fragmento")
        if parsed.query:
            raise ComplianceError("INSTAGRAM_MEDIA_BASE_URL nao pode ter query assinada")
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            address = None
        if hostname == "localhost" or hostname.endswith(".local") or (address and not address.is_global):
            raise ComplianceError("INSTAGRAM_MEDIA_BASE_URL precisa ser publicamente acessivel pela Meta")
        normalized_hosts = {
            item.strip().lower()
            for item in self.settings.instagram_asset_allowed_hosts
            if item.strip()
        }
        if hostname not in normalized_hosts:
            raise ComplianceError("host de INSTAGRAM_MEDIA_BASE_URL nao esta em INSTAGRAM_ASSET_ALLOWED_HOSTS")
        encoded_path = "/".join(quote(part, safe="") for part in relative_asset.split("/"))
        return f"{base_url.rstrip('/')}/{encoded_path}"

    def _reel_queue(self, queue_id: int) -> dict[str, Any]:
        row = self.db.get_social_queue(queue_id)
        if row is None:
            raise ValueError(f"item social {queue_id} nao encontrado")
        queue = dict(row)
        if queue["channel"] != "instagram" or queue["format"] != "reel":
            raise ComplianceError("somente Instagram Reel possui contrato de publicacao habilitado")
        if queue["status"] != "READY_FOR_PUBLISH":
            raise ComplianceError(f"item social precisa estar READY_FOR_PUBLISH; atual: {queue['status']}")
        validate_offer(queue)
        validate_distribution(queue, "instagram", self.settings)
        validate_content(str(queue["body"]), allow_http=False)
        try:
            assets = json.loads(str(queue["assets_json"]))
        except json.JSONDecodeError as exc:
            raise ComplianceError("assets_json invalido") from exc
        if not isinstance(assets, list):
            raise ComplianceError("assets_json precisa ser uma lista")
        video_assets = [str(item) for item in assets if str(item).lower().endswith(".mp4")]
        if len(video_assets) != 1:
            raise ComplianceError("Reel precisa ter exatamente um MP4 pronto")
        root = self.settings.creatives_path.resolve()
        local_asset = (root / video_assets[0]).resolve()
        try:
            local_asset.relative_to(root)
        except ValueError as exc:
            raise ComplianceError("asset local fora de CREATIVES_PATH") from exc
        if not local_asset.is_file() or local_asset.stat().st_size <= 0:
            raise ComplianceError("MP4 local ausente ou vazio")
        queue["local_asset"] = str(local_asset)
        queue["relative_asset"] = local_asset.relative_to(root).as_posix()
        return queue

    def _eligible_reel(self, queue_id: int) -> dict[str, Any]:
        queue = self._reel_queue(queue_id)
        queue["asset_url"] = self._public_asset_url(queue["relative_asset"])
        return queue

    @staticmethod
    def _require_approval(approved: bool) -> None:
        if not approved:
            raise ComplianceError("confirmacao editorial explicita e obrigatoria")

    def create_reel(self, queue_id: int, *, approved: bool) -> dict[str, Any]:
        self._require_approval(approved)
        queue = self._eligible_reel(queue_id)
        if self.settings.dry_run:
            return {
                "queue_id": queue_id,
                "status": "SIMULATED",
                "network_calls": 0,
                "media_type": "REELS",
                "asset_url": queue["asset_url"],
            }
        user_id = self._real_configuration()
        try:
            self.db.claim_instagram_container_creation(queue_id, queue["asset_url"])
        except ValueError as exc:
            raise ComplianceError(str(exc)) from exc
        try:
            container_id = self._client().create_reel(
                user_id,
                queue["asset_url"],
                str(queue["body"]),
            )
        except InstagramGraphError as exc:
            if exc.ambiguous:
                self.db.mark_instagram_container_creation_ambiguous(queue_id, exc.code)
            else:
                self.db.release_instagram_container_creation(queue_id)
            raise
        except Exception:
            self.db.mark_instagram_container_creation_ambiguous(
                queue_id,
                "UNEXPECTED_CREATE_FAILURE",
            )
            raise
        publication = self.db.complete_instagram_container_creation(
            queue_id,
            queue["asset_url"],
            container_id,
        )
        return dict(publication)

    def check_status(self, queue_id: int) -> dict[str, Any]:
        publication = self.db.instagram_publication(queue_id)
        if publication is None:
            if self.settings.dry_run:
                return {"queue_id": queue_id, "status": "SIMULATED", "network_calls": 0}
            raise ValueError("container Instagram ainda nao foi criado")
        current = dict(publication)
        if current["status"] == "PUBLISHED":
            return current
        self._real_configuration()
        remote_status = self._client().container_status(str(current["container_id"]))
        if current["status"] in {"PUBLISHING", "PUBLISH_AMBIGUOUS"}:
            current["remote_status"] = remote_status
            current["status"] = "RECONCILIATION_REQUIRED"
            return current
        mapped = {
            "IN_PROGRESS": ("PROCESSING", None),
            "FINISHED": ("READY_TO_PUBLISH", None),
            "ERROR": ("PROCESSING_FAILED", "CONTAINER_ERROR"),
            "EXPIRED": ("PROCESSING_FAILED", "CONTAINER_EXPIRED"),
            "PUBLISHED": ("PROCESSING_FAILED", "UNEXPECTED_REMOTE_PUBLISHED"),
        }
        status, error_code = mapped[remote_status]
        return dict(self.db.update_instagram_processing(queue_id, status, error_code))

    def publish_reel(self, queue_id: int, *, approved: bool) -> dict[str, Any]:
        self._require_approval(approved)
        if self.settings.dry_run:
            self._reel_queue(queue_id)
            return {"queue_id": queue_id, "status": "SIMULATED", "network_calls": 0}
        existing = self.db.instagram_publication(queue_id)
        if existing is None:
            raise ValueError("container Instagram ainda nao foi criado")
        queue = self._eligible_reel(queue_id)
        if queue["asset_url"] != existing["asset_url"]:
            raise ComplianceError("mapeamento publico do MP4 mudou apos a criacao do container")
        raise ComplianceError(
            "media_publish bloqueado para conteudo afiliado: o contrato oficial acessivel "
            "nao oferece parametro confirmado para aplicar o rotulo de parceria paga"
        )

    def reconcile_container_creation(
        self,
        queue_id: int,
        *,
        container_id: str | None,
        confirmed_not_created: bool,
        note: str,
    ) -> dict[str, Any]:
        if bool(container_id) == bool(confirmed_not_created):
            raise ValueError("informe container_id ou confirme ausencia, mas nao ambos")
        if not note.strip():
            raise ValueError("nota de reconciliacao e obrigatoria")
        if container_id and not ID_PATTERN.fullmatch(container_id):
            raise ValueError("container_id invalido")
        return self.db.reconcile_instagram_container_creation(
            queue_id,
            container_id=container_id,
            confirmed_not_created=confirmed_not_created,
            note=note,
        )

    def reconcile(
        self,
        queue_id: int,
        *,
        published_media_id: str | None,
        confirmed_not_published: bool,
        note: str,
    ) -> dict[str, Any]:
        if bool(published_media_id) == bool(confirmed_not_published):
            raise ValueError("informe media_id publicado ou confirme ausencia, mas nao ambos")
        if not note.strip():
            raise ValueError("nota de reconciliacao e obrigatoria")
        if published_media_id:
            if not ID_PATTERN.fullmatch(published_media_id):
                raise ValueError("published_media_id invalido")
            return dict(
                self.db.mark_instagram_published(
                    queue_id,
                    published_media_id,
                    reconciliation_note=note[:500],
                )
            )
        return dict(self.db.reconcile_instagram_not_published(queue_id, note))
