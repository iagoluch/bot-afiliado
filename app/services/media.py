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
from app.services.llm import heavy_work_slot
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


def _text_size(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
) -> tuple[int, int]:
    box = draw.textbbox((0, 0), text, font=font)
    return max(0, box[2] - box[0]), max(0, box[3] - box[1])


def _wrap_lines(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    width: int,
) -> list[str]:
    """Quebra o texto integralmente sem permitir linhas fora do canvas."""
    words = str(text).split()
    if not words:
        return []

    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if _text_size(draw, candidate, font)[0] <= width:
            current = candidate
            continue

        if current:
            lines.append(current)
            current = ""

        if _text_size(draw, word, font)[0] <= width:
            current = word
            continue

        chunk = ""
        for char in word:
            candidate = chunk + char
            if chunk and _text_size(draw, candidate, font)[0] > width:
                lines.append(chunk)
                chunk = char
            else:
                chunk = candidate
        current = chunk

    if current:
        lines.append(current)
    return lines


def _block_height(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    font: ImageFont.ImageFont,
    spacing: int,
) -> int:
    if not lines:
        return 0
    return sum(_text_size(draw, line, font)[1] for line in lines) + spacing * max(0, len(lines) - 1)


def _fit_text_block(
    draw: ImageDraw.ImageDraw,
    text: str,
    *,
    max_width: int,
    max_height: int,
    start_size: int,
    min_size: int,
    bold: bool = False,
    max_lines: int | None = None,
    spacing: int = 14,
) -> tuple[ImageFont.ImageFont, list[str]]:
    """Faz wrapping e reduz a fonte até todo o texto caber no bloco."""
    floor = max(18, min_size)
    for size in range(max(start_size, floor), floor - 1, -2):
        font = _font(size, bold=bold)
        lines = _wrap_lines(draw, text, font, max_width)
        if max_lines is not None and len(lines) > max_lines:
            continue
        if _block_height(draw, lines, font, spacing) <= max_height:
            return font, lines

    # Nunca troca clipping por reticências: em conteúdo publicável o texto
    # precisa permanecer completo, mesmo se isso exigir fonte menor.
    for size in range(floor - 2, 17, -2):
        font = _font(size, bold=bold)
        lines = _wrap_lines(draw, text, font, max_width)
        if _block_height(draw, lines, font, spacing) <= max_height:
            return font, lines

    font = _font(18, bold=bold)
    return font, _wrap_lines(draw, text, font, max_width)


def _draw_centered_lines(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    y: int,
    font: ImageFont.ImageFont,
    fill: str,
    canvas_width: int,
    spacing: int = 14,
) -> int:
    for line in lines:
        box = draw.textbbox((0, 0), line, font=font)
        line_width = box[2] - box[0]
        line_height = box[3] - box[1]
        x = (canvas_width - line_width) // 2 - box[0]
        draw.text((x, y - box[1]), line, font=font, fill=fill)
        y += line_height + spacing
    return y


def _palette(variant: str) -> dict[str, str]:
    if variant == "b":
        return {
            "background": "#f8fafc",
            "accent": "#0f766e",
            "disclosure": "#0f766e",
            "title": "#0f172a",
            "primary": "#047857",
            "secondary": "#475569",
            "panel": "#e2e8f0",
            "panel_text": "#64748b",
        }
    return {
        "background": "#0f172a",
        "accent": "#22c55e",
        "disclosure": "#86efac",
        "title": "#f8fafc",
        "primary": "#4ade80",
        "secondary": "#cbd5e1",
        "panel": "#f8fafc",
        "panel_text": "#64748b",
    }


def _product_panel(
    canvas: Image.Image,
    product: Image.Image | None,
    box: tuple[int, int, int, int],
    *,
    variant: str = "a",
) -> None:
    x1, y1, x2, y2 = box
    draw = ImageDraw.Draw(canvas)
    colors = _palette(variant)
    draw.rounded_rectangle(box, radius=34, fill=colors["panel"])
    if product is None:
        label = "Produto sem imagem"
        note = "Prévia de desenvolvimento"
        label_font = _font(34, bold=True)
        note_font = _font(25)
        label_box = draw.textbbox((0, 0), label, font=label_font)
        note_box = draw.textbbox((0, 0), note, font=note_font)
        center_y = (y1 + y2) // 2
        draw.text(
            ((x1 + x2 - (label_box[2] - label_box[0])) // 2, center_y - 34),
            label,
            font=label_font,
            fill=colors["panel_text"],
        )
        draw.text(
            ((x1 + x2 - (note_box[2] - note_box[0])) // 2, center_y + 18),
            note,
            font=note_font,
            fill=colors["panel_text"],
        )
        return

    inner = (max(80, x2 - x1 - 80), max(80, y2 - y1 - 80))
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
    role: str = "generic",
    variant: str = "a",
) -> Image.Image:
    width, height = size
    vertical = height > width
    colors = _palette(variant)
    canvas = Image.new("RGB", size, colors["background"])
    draw = ImageDraw.Draw(canvas)

    accent_height = max(14, height // 90)
    draw.rectangle((0, 0, width, accent_height), fill=colors["accent"])
    disclosure_font = _font(max(22, width // 38), bold=True)
    draw.text((int(width * 0.055), int(height * 0.032)), disclosure, font=disclosure_font, fill=colors["disclosure"])

    safe_x = int(width * 0.09)
    text_width = width - safe_x * 2

    if vertical:
        # Mantém conteúdo principal longe das áreas de controles das plataformas.
        panel_top = int(height * 0.105)
        panel_bottom = int(height * (0.43 if product is not None else 0.33))
        text_top = panel_bottom + int(height * 0.035)
        text_bottom = int(height * 0.80)
    else:
        panel_top = int(height * 0.11)
        panel_bottom = int(height * (0.56 if product is not None else 0.44))
        text_top = panel_bottom + int(height * 0.035)
        text_bottom = int(height * 0.94)

    _product_panel(
        canvas,
        product,
        (safe_x, panel_top, width - safe_x, panel_bottom),
        variant=variant,
    )

    available = max(120, text_bottom - text_top)
    title_ratio = 0.24 if role not in {"hook", "cta"} else 0.18
    primary_ratio = 0.48 if role in {"hook", "product", "benefit", "cta"} else 0.42
    secondary_ratio = max(0.16, 1.0 - title_ratio - primary_ratio)

    y = text_top
    title_font, title_lines = _fit_text_block(
        draw,
        title,
        max_width=text_width,
        max_height=int(available * title_ratio),
        start_size=54 if vertical else 52,
        min_size=30,
        bold=True,
        max_lines=2,
        spacing=10,
    )
    y = _draw_centered_lines(draw, title_lines, y, title_font, colors["title"], width, spacing=10)

    y += int(height * 0.014)
    primary_start = 96 if role == "price" else (82 if role in {"hook", "benefit", "cta"} else 74)
    primary_font, primary_lines = _fit_text_block(
        draw,
        primary,
        max_width=text_width,
        max_height=int(available * primary_ratio),
        start_size=primary_start,
        min_size=34,
        bold=True,
        max_lines=4,
        spacing=12,
    )
    y = _draw_centered_lines(draw, primary_lines, y, primary_font, colors["primary"], width, spacing=12)

    if secondary.strip():
        y += int(height * 0.010)
        secondary_font, secondary_lines = _fit_text_block(
            draw,
            secondary,
            max_width=text_width,
            max_height=int(available * secondary_ratio),
            start_size=38 if vertical else 36,
            min_size=24,
            max_lines=3,
            spacing=8,
        )
        _draw_centered_lines(draw, secondary_lines, y, secondary_font, colors["secondary"], width, spacing=8)

    return canvas


def _comparison_sheet(left: Image.Image, right: Image.Image, label: str) -> Image.Image:
    """Prévia A/B para revisão humana; nunca é usada como asset de publicação."""
    header = 72
    width = left.width + right.width
    height = max(left.height, right.height) + header
    sheet = Image.new("RGB", (width, height), "#111827")
    draw = ImageDraw.Draw(sheet)
    font = _font(30, bold=True)
    draw.text((28, 20), f"{label} · A", font=font, fill="#f8fafc")
    draw.text((left.width + 28, 20), f"{label} · B", font=font, fill="#f8fafc")
    sheet.paste(left, (0, header))
    sheet.paste(right, (left.width, header))
    return sheet


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
            with heavy_work_slot():
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
