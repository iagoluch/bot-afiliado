from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable
from urllib.error import HTTPError, URLError


MAX_RESPONSE_BYTES = 64 * 1024
MAX_MESSAGE_CHARS = 4096
MAX_MEDIA_CAPTION_CHARS = 1024


class TelegramAPIError(RuntimeError):
    pass


class TelegramMediaRejected(TelegramAPIError):
    """Telegram confirmed that this media was not accepted."""


class TelegramDeliveryUncertain(TelegramAPIError):
    """A request may have reached Telegram, but no delivery result was confirmed."""


class TelegramRateLimit(TelegramAPIError):
    def __init__(self, retry_after: int):
        super().__init__("Telegram aplicou limite de envio")
        self.retry_after = retry_after


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def _default_open(request: urllib.request.Request, timeout: float):
    return urllib.request.build_opener(_NoRedirectHandler()).open(request, timeout=timeout)


def _read_payload(response) -> dict:  # noqa: ANN001
    try:
        raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise TelegramAPIError("resposta Telegram excedeu o limite")
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TelegramAPIError("resposta Telegram invalida") from exc
    if not isinstance(payload, dict):
        raise TelegramAPIError("resposta Telegram invalida")
    return payload


def _retry_after(payload: dict) -> int:
    parameters = payload.get("parameters")
    value = parameters.get("retry_after") if isinstance(parameters, dict) else None
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        seconds = 60
    return max(1, min(seconds, 3600))


@dataclass(frozen=True)
class TelegramResult:
    message_id: str
    dry_run: bool


class TelegramClient:
    def __init__(
        self,
        token: str | None,
        chat_id: str | None,
        *,
        dry_run: bool,
        opener: Callable = _default_open,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.token = token
        self.chat_id = chat_id
        self.dry_run = dry_run
        self.opener = opener
        self._clock = clock
        self._sleep = sleep
        self._last_attempt_at: float | None = None

    def send_message(self, text: str) -> TelegramResult:
        if not 1 <= len(text) <= MAX_MESSAGE_CHARS:
            raise TelegramAPIError(
                f"mensagem Telegram deve ter entre 1 e {MAX_MESSAGE_CHARS} caracteres"
            )
        return self._send("sendMessage", {"text": text})

    def send_photo(self, text: str, photo_url: str) -> TelegramResult:
        """Single Telegram message with original photo; fallback only on definite rejection.

        A timeout/5xx is ambiguous: do not send text as a second message.
        """
        if not photo_url.startswith("https://") or len(text) > MAX_MEDIA_CAPTION_CHARS:
            return self.send_message(text)
        try:
            return self._send("sendPhoto", {"photo": photo_url, "caption": text})
        except TelegramMediaRejected:
            return self.send_message(text)

    def _send(self, method: str, params: dict[str, str]) -> TelegramResult:
        if self.dry_run:
            return TelegramResult(message_id="dry-run", dry_run=True)
        if not self.token or not self.chat_id:
            raise RuntimeError("TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID sao obrigatorios fora de DRY_RUN")
        if self._last_attempt_at is not None:
            delay = self._last_attempt_at + 1.05 - self._clock()
            if delay > 0:
                self._sleep(delay)
        self._last_attempt_at = self._clock()
        endpoint = f"https://api.telegram.org/bot{self.token}/{method}"
        body = urllib.parse.urlencode({"chat_id": self.chat_id, **params}).encode()
        request = urllib.request.Request(endpoint, data=body, method="POST")
        try:
            with self.opener(request, timeout=20) as response:
                try:
                    payload = _read_payload(response)
                except TelegramAPIError as exc:
                    raise TelegramDeliveryUncertain("resposta Telegram sem confirmacao de entrega") from exc
        except HTTPError as exc:
            try:
                payload = _read_payload(exc)
            except TelegramAPIError:
                payload = {}
            if exc.code == 429 or payload.get("error_code") == 429:
                raise TelegramRateLimit(_retry_after(payload)) from None
            if method == "sendPhoto" and exc.code == 400:
                raise TelegramMediaRejected("Telegram recusou a imagem") from None
            if exc.code >= 500 or exc.code in {408, 425}:
                raise TelegramDeliveryUncertain("erro Telegram sem confirmacao de entrega") from None
            raise TelegramAPIError(f"Telegram recusou a mensagem (HTTP {exc.code})") from None
        except (URLError, TimeoutError, OSError):
            raise TelegramDeliveryUncertain("falha de transporte sem confirmacao de entrega") from None
        if payload.get("error_code") == 429:
            raise TelegramRateLimit(_retry_after(payload))
        if isinstance(payload.get("error_code"), int) and payload["error_code"] >= 500:
            raise TelegramDeliveryUncertain("erro Telegram sem confirmacao de entrega")
        if not payload.get("ok"):
            raise TelegramAPIError("Telegram recusou a mensagem")
        if not isinstance(payload.get("result"), dict) or not payload["result"].get("message_id"):
            raise TelegramDeliveryUncertain("resposta Telegram sem message_id")
        return TelegramResult(message_id=str(payload["result"]["message_id"]), dry_run=False)
