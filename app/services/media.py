from __future__ import annotations

import hashlib
import io
import json
import shutil
import subprocess
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError

from app.services.content import brl, verified_discount
from app.services.site import offer_slug


MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
MAX_IMAGE_DIMENSION = 6_000


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _approved_image_url(value: str, allowed_hosts: tuple[str, ...] = ()) -> bool:
    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower()
    configured = {entry.lower().strip(".") for entry in allowed_hosts if entry}
    return parsed.scheme == "https" and (
        host == "susercontent.com"
        or host.endswith(".susercontent.com")
        or host == "shopee.com.br"
        or host.endswith(".shopee.com.br")
        or host == "http2.mlstatic.com"
        or any(host == entry or host.endswith(f".{entry}") for entry in configured)
    )


def load_approved_product_image(url: str, allowed_hosts: tuple[str, ...] = ()) -> Image.Image | None:
    if not _approved_image_url(url, allowed_hosts):
        return None
    request = Request(url, headers={"User-Agent": "BOT-AFILIADO/0.2 image-fetch"})
    try:
        # Não segue redirects: a validação do host precisa ocorrer antes de
        # qualquer conexão, inclusive após uma resposta 3xx.
        with build_opener(_NoRedirect()).open(request, timeout=10) as response:
            content_type = response.headers.get_content_type()
            declared = response.headers.get("Content-Length")
            if not content_type.startswith("image/") or (declared and int(declared) > MAX_IMAGE_BYTES):
                return None
            data = response.read(MAX_IMAGE_BYTES + 1)
        if len(data) > MAX_IMAGE_BYTES:
            return None
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as source:
                width, height = source.size
                if width > MAX_IMAGE_DIMENSION or height > MAX_IMAGE_DIMENSION or width * height > MAX_IMAGE_PIXELS:
                    return None
                source.verify()
            with Image.open(io.BytesIO(data)) as source:
                source.thumbnail((2_000, 2_000), Image.Resampling.LANCZOS)
                return ImageOps.exif_transpose(source).convert("RGB")
    except (OSError, ValueError, HTTPError, URLError, UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        return None


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = (
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
        "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
    )
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _fit_lines(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, width: int, max_lines: int = 3) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if not current or draw.textbbox((0, 0), candidate, font=font)[2] <= width:
            current = candidate
        else:
            lines.append(current)
            current = word
            if len(lines) == max_lines - 1:
                break
    if current and len(lines) < max_lines:
        remaining = " ".join(words[sum(len(line.split()) for line in lines):])
        while remaining and draw.textbbox((0, 0), remaining, font=font)[2] > width:
            remaining = remaining[:-1]
        if remaining != " ".join(words[sum(len(line.split()) for line in lines):]):
            remaining = remaining.rstrip() + "…"
        lines.append(remaining)
    return lines


def _draw_centered_lines(draw: ImageDraw.ImageDraw, lines: list[str], y: int, font: ImageFont.ImageFont, fill: str, canvas_width: int, spacing: int = 14) -> int:
    for line in lines:
        box = draw.textbbox((0, 0), line, font=font)
        x = (canvas_width - (box[2] - box[0])) // 2
        draw.text((x, y), line, font=font, fill=fill)
        y += box[3] - box[1] + spacing
    return y


def _product_panel(canvas: Image.Image, product: Image.Image | None, box: tuple[int, int, int, int]) -> None:
    x1, y1, x2, y2 = box
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle(box, radius=36, fill="#ffffff")
    if product is None:
        label = "IMAGEM DO PRODUTO\nNAO DISPONIVEL"
        font = _font(42, bold=True)
        bounds = draw.multiline_textbbox((0, 0), label, font=font, align="center", spacing=12)
        draw.multiline_text(((x1 + x2 - bounds[2]) // 2, (y1 + y2 - bounds[3]) // 2), label, font=font, fill="#64748b", align="center", spacing=12)
        return
    inner = (x2 - x1 - 80, y2 - y1 - 80)
    fitted = ImageOps.contain(product, inner, method=Image.Resampling.LANCZOS)
    canvas.paste(fitted, (x1 + (x2 - x1 - fitted.width) // 2, y1 + (y2 - y1 - fitted.height) // 2))


def _card(
    size: tuple[int, int],
    *,
    title: str,
    primary: str,
    secondary: str,
    product: Image.Image | None = None,
    disclosure: str = "PUBLICIDADE",
) -> Image.Image:
    width, height = size
    canvas = Image.new("RGB", size, "#0f172a")
    draw = ImageDraw.Draw(canvas)
    accent_height = max(16, height // 80)
    draw.rectangle((0, 0, width, accent_height), fill="#22c55e")
    draw.text((width * 0.06, height * 0.035), disclosure, font=_font(max(20, width // 38), bold=True), fill="#86efac")

    panel_top = int(height * 0.10)
    panel_bottom = int(height * (0.55 if height > width else 0.58))
    _product_panel(canvas, product, (int(width * 0.08), panel_top, int(width * 0.92), panel_bottom))

    y = panel_bottom + int(height * 0.035)
    title_font = _font(max(34, width // 20), bold=True)
    y = _draw_centered_lines(draw, _fit_lines(draw, title, title_font, int(width * 0.84), 3), y, title_font, "#f8fafc", width)
    primary_font = _font(max(42, width // 14), bold=True)
    y += int(height * 0.018)
    y = _draw_centered_lines(draw, [primary], y, primary_font, "#4ade80", width)
    secondary_font = _font(max(24, width // 28))
    y += int(height * 0.012)
    _draw_centered_lines(draw, _fit_lines(draw, secondary, secondary_font, int(width * 0.84), 2), y, secondary_font, "#cbd5e1", width)
    return canvas


@dataclass(frozen=True, slots=True)
class VideoResult:
    status: str
    path: str | None
    detail: str | None


@dataclass(frozen=True, slots=True)
class CreativeAssets:
    root: Path
    feed: tuple[Path, ...]
    story: Path
    reel_frames: tuple[Path, ...]
    reel_thumbnail: Path
    reel_video: VideoResult
    tiktok_frames: tuple[Path, ...]
    tiktok_thumbnail: Path
    tiktok_video: VideoResult


class CreativeGenerator:
    def __init__(
        self,
        root: Path,
        *,
        ffmpeg_path: str | None = None,
        image_loader: Callable[[str], Image.Image | None] | None = None,
        allowed_image_hosts: tuple[str, ...] = (),
    ):
        self.root = root
        self.ffmpeg_path = ffmpeg_path
        self.image_loader = image_loader or (lambda url: load_approved_product_image(url, allowed_image_hosts))

    def _product_image(self, offer: dict[str, Any]) -> Image.Image | None:
        try:
            urls = json.loads(offer.get("image_urls_json") or "[]")
        except (TypeError, json.JSONDecodeError):
            return None
        for url in urls:
            image = self.image_loader(str(url))
            if image is not None:
                return image
        return None

    def _output_dir(self, offer: dict[str, Any], campaign_id: str) -> Path:
        fingerprint = hashlib.sha256(
            f"{offer['id']}:{offer['current_price_cents']}:{offer.get('coupon') or ''}:{campaign_id}:v1".encode()
        ).hexdigest()[:12]
        output = self.root / f"{offer_slug(offer)}-{fingerprint}"
        output.mkdir(parents=True, exist_ok=True)
        return output

    @staticmethod
    def _save(image: Image.Image, path: Path) -> Path:
        image.save(path, format="PNG", optimize=True)
        return path

    def _video(self, frames: tuple[Path, ...], durations: tuple[float, ...], output: Path) -> VideoResult:
        executable = self.ffmpeg_path or shutil.which("ffmpeg")
        if not executable or not Path(executable).is_file():
            return VideoResult("FFMPEG_UNAVAILABLE", None, "Configure FFMPEG_PATH com o executavel FFmpeg para gerar o MP4.")
        command = [str(executable), "-hide_banner", "-loglevel", "error", "-y"]
        for frame, duration in zip(frames, durations, strict=True):
            command.extend(("-loop", "1", "-t", f"{duration:g}", "-i", str(frame)))
        command.extend((
            "-filter_complex", f"concat=n={len(frames)}:v=1:a=0,format=yuv420p",
            "-r", "30", "-c:v", "libx264", "-preset", "veryfast", "-movflags", "+faststart", str(output),
        ))
        try:
            subprocess.run(command, check=True, capture_output=True, text=True, timeout=180)
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            detail = exc.stderr.strip() if isinstance(exc, subprocess.CalledProcessError) and exc.stderr else str(exc)
            return VideoResult("FFMPEG_FAILED", None, detail[:500])
        return VideoResult("READY", str(output), None)

    def generate(
        self,
        offer: dict[str, Any],
        campaign_id: str,
        reel_script: tuple[dict[str, Any], ...],
        tiktok_script: tuple[dict[str, Any], ...],
        *,
        duration_scale: float = 1.0,
    ) -> CreativeAssets:
        output = self._output_dir(offer, campaign_id)
        product = self._product_image(offer)
        title = str(offer["title"])
        price = brl(offer["current_price_cents"])
        coupon = f"Cupom: {offer['coupon']}" if offer.get("coupon") else "Confira as condições atuais"
        verified = verified_discount(offer)
        discount = f"{verified[1]:.0f}% OFF comprovado" if verified else "Oferta selecionada"

        feed = (
            self._save(_card((1080, 1080), title=title, primary=price, secondary="Preco informado na ultima atualizacao", product=product), output / "instagram-feed-1.png"),
            self._save(_card((1080, 1080), title=title, primary=discount, secondary=str(offer.get("shipping") or offer.get("category") or "Confira os detalhes"), product=product), output / "instagram-feed-2.png"),
            self._save(_card((1080, 1080), title=title, primary=coupon, secondary="Confira preco e disponibilidade no link", product=product), output / "instagram-feed-3.png"),
        )
        story = self._save(_card((1080, 1920), title=title, primary=price, secondary=f"{coupon}. Confira no link.", product=product), output / "instagram-story.png")

        def render_frames(prefix: str, script: tuple[dict[str, Any], ...]) -> tuple[Path, ...]:
            paths: list[Path] = []
            for index, scene in enumerate(script, start=1):
                image = _card((1080, 1920), title=title, primary=str(scene["text"]), secondary=f"Cena {index}/{len(script)}", product=product)
                paths.append(self._save(image, output / f"{prefix}-frame-{index}.png"))
            return tuple(paths)

        reel_frames = render_frames("instagram-reel", reel_script)
        tiktok_frames = render_frames("tiktok", tiktok_script)
        reel_thumbnail = self._save(_card((1080, 1920), title=title, primary=price, secondary="Confira as condições", product=product), output / "instagram-reel-thumbnail.png")
        tiktok_thumbnail = self._save(_card((1080, 1920), title=title, primary=price, secondary="Rascunho para revisão", product=product), output / "tiktok-thumbnail.png")
        reel_durations = tuple(max(0.1, (float(s["end"]) - float(s["start"])) * duration_scale) for s in reel_script)
        tiktok_durations = tuple(max(0.1, (float(s["end"]) - float(s["start"])) * duration_scale) for s in tiktok_script)
        reel_video = self._video(reel_frames, reel_durations, output / "instagram-reel.mp4")
        tiktok_video = self._video(tiktok_frames, tiktok_durations, output / "tiktok-draft.mp4")
        return CreativeAssets(output, feed, story, reel_frames, reel_thumbnail, reel_video, tiktok_frames, tiktok_thumbnail, tiktok_video)
