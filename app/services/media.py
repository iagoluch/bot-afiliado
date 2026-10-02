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
    product_scale: float = 1.0,
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

    scale = max(1.0, min(float(product_scale), 1.05))
    inner = (
        max(80, int((x2 - x1 - 80) * scale)),
        max(80, int((y2 - y1 - 80) * scale)),
    )
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
    product_scale: float = 1.0,
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
        product_scale=product_scale,
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
    ab_previews: tuple[Path, ...]


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
            f"{offer['id']}:{offer['current_price_cents']}:{offer.get('coupon') or ''}:{campaign_id}:v3".encode()
        ).hexdigest()[:12]
        output = self.root / f"{offer_slug(offer)}-{fingerprint}"
        output.mkdir(parents=True, exist_ok=True)
        return output

    @staticmethod
    def _save(image: Image.Image, path: Path) -> Path:
        image.save(path, format="PNG", optimize=True)
        return path

    @staticmethod
    def _scene_secondary(role: str) -> str:
        # Benefit e CTA já carregam a mensagem completa no texto principal.
        # Repeti-la em uma linha secundária reduz hierarquia e parecia bug visual.
        if role == "price":
            return "Preço informado na última atualização"
        return ""

    def _animated_video_plan(
        self,
        *,
        output: Path,
        prefix: str,
        title: str,
        product: Image.Image | None,
        script: tuple[dict[str, Any], ...],
        duration_scale: float,
        variant: str = "a",
    ) -> tuple[tuple[Path, ...], tuple[float, ...], Path]:
        """Gera poucos keyframes: zoom do produto, texto progressivo e fade entre cenas."""
        root = output / ".video-keyframes" / prefix
        root.mkdir(parents=True, exist_ok=True)

        scenes: list[tuple[float, list[Image.Image]]] = []
        steps = 8
        for scene in script:
            role = str(scene.get("role") or "generic")
            primary = str(scene["text"])
            secondary = self._scene_secondary(role)
            duration = max(
                0.1,
                (float(scene["end"]) - float(scene["start"])) * duration_scale,
            )
            frames: list[Image.Image] = []
            for step in range(steps):
                progress = step / (steps - 1)
                zoom = 1.0 + (0.035 * progress)
                full = _card(
                    (1080, 1920),
                    title="" if role == "hook" else title,
                    primary=primary,
                    secondary=secondary,
                    product=product,
                    role=role,
                    variant=variant,
                    product_scale=zoom,
                )
                if progress >= 1.0:
                    frames.append(full)
                    continue
                base = _card(
                    (1080, 1920),
                    title="" if role == "hook" else title,
                    primary="",
                    secondary="",
                    product=product,
                    role=role,
                    variant=variant,
                    product_scale=zoom,
                )
                # Texto conclui a entrada cedo e o restante da cena mantém
                # apenas o zoom muito suave no produto.
                text_alpha = min(1.0, progress / 0.65)
                frames.append(Image.blend(base, full, text_alpha))
            scenes.append((duration, frames))

        paths: list[Path] = []
        durations: list[float] = []
        fade_steps = 2
        for scene_index, (scene_duration, frames) in enumerate(scenes):
            has_next = scene_index + 1 < len(scenes)
            fade_duration = min(0.20, scene_duration * 0.12) if has_next else 0.0
            body_duration = max(0.06, scene_duration - fade_duration)
            animation_window = min(1.20, body_duration * 0.60)
            frame_duration = animation_window / len(frames)
            hold_duration = max(0.0, body_duration - animation_window)

            for frame_index, frame in enumerate(frames, start=1):
                path = root / f"scene-{scene_index + 1:02d}-{frame_index:02d}.png"
                self._save(frame, path)
                paths.append(path)
                duration = frame_duration
                if frame_index == len(frames):
                    duration += hold_duration
                durations.append(duration)

            if has_next and fade_duration > 0:
                current = frames[-1]
                next_start = scenes[scene_index + 1][1][0]
                for fade_index in range(1, fade_steps + 1):
                    alpha = fade_index / (fade_steps + 1)
                    blended = Image.blend(current, next_start, alpha)
                    path = root / f"fade-{scene_index + 1:02d}-{fade_index:02d}.png"
                    self._save(blended, path)
                    paths.append(path)
                    durations.append(fade_duration / fade_steps)

        return tuple(paths), tuple(durations), root

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

        feed_cards = (
            dict(primary=price, secondary="Preço informado na última atualização", role="price"),
            dict(primary=discount, secondary=str(offer.get("shipping") or offer.get("category") or "Confira os detalhes"), role="benefit"),
            dict(primary=coupon, secondary="Confira preço e disponibilidade no link", role="benefit"),
        )
        feed = tuple(
            self._save(
                _card(
                    (1080, 1080),
                    title=title,
                    primary=card["primary"],
                    secondary=card["secondary"],
                    product=product,
                    role=card["role"],
                    variant="a",
                ),
                output / f"instagram-feed-{index}.png",
            )
            for index, card in enumerate(feed_cards, start=1)
        )
        story = self._save(
            _card(
                (1080, 1920),
                title=title,
                primary=price,
                secondary=f"{coupon}. Confira no link.",
                product=product,
                role="price",
                variant="a",
            ),
            output / "instagram-story.png",
        )

        def render_frames(
            prefix: str,
            script: tuple[dict[str, Any], ...],
            *,
            variant: str = "a",
            save: bool = True,
        ) -> tuple[Path, ...] | tuple[Image.Image, ...]:
            rendered: list[Path] | list[Image.Image] = []
            for index, scene in enumerate(script, start=1):
                role = str(scene.get("role") or "generic")
                secondary = self._scene_secondary(role)
                image = _card(
                    (1080, 1920),
                    title="" if role == "hook" else title,
                    primary=str(scene["text"]),
                    secondary=secondary,
                    product=product,
                    role=role,
                    variant=variant,
                )
                if save:
                    rendered.append(self._save(image, output / f"{prefix}-frame-{index}.png"))
                else:
                    rendered.append(image)
            return tuple(rendered)

        reel_frames = render_frames("instagram-reel", reel_script)
        tiktok_frames = render_frames("tiktok", tiktok_script)
        assert all(isinstance(path, Path) for path in reel_frames)
        assert all(isinstance(path, Path) for path in tiktok_frames)
        reel_frames = tuple(reel_frames)
        tiktok_frames = tuple(tiktok_frames)

        reel_thumbnail = self._save(
            _card(
                (1080, 1920),
                title=title,
                primary=price,
                secondary="Confira as condições",
                product=product,
                role="price",
                variant="a",
            ),
            output / "instagram-reel-thumbnail.png",
        )
        tiktok_thumbnail = self._save(
            _card(
                (1080, 1920),
                title=title,
                primary=price,
                secondary="Rascunho para revisão",
                product=product,
                role="price",
                variant="a",
            ),
            output / "tiktok-thumbnail.png",
        )

        # A/B é somente uma comparação visual de revisão. A variante A segue como
        # asset canônico e a variante B nunca entra automaticamente em publicação.
        feed_b = _card(
            (1080, 1080),
            title=title,
            primary=feed_cards[0]["primary"],
            secondary=feed_cards[0]["secondary"],
            product=product,
            role=feed_cards[0]["role"],
            variant="b",
        )
        story_b = _card(
            (1080, 1920),
            title=title,
            primary=price,
            secondary=f"{coupon}. Confira no link.",
            product=product,
            role="price",
            variant="b",
        )
        reel_b_frames = render_frames("instagram-reel-b", reel_script[:1], variant="b", save=False)
        tiktok_b_frames = render_frames("tiktok-b", tiktok_script[:1], variant="b", save=False)
        reel_b = reel_b_frames[0] if reel_b_frames else _card(
            (1080, 1920), title=title, primary=price, secondary="", product=product, role="hook", variant="b",
        )
        tiktok_b = tiktok_b_frames[0] if tiktok_b_frames else _card(
            (1080, 1920), title=title, primary=price, secondary="", product=product, role="hook", variant="b",
        )
        assert isinstance(reel_b, Image.Image)
        assert isinstance(tiktok_b, Image.Image)

        with Image.open(feed[0]) as feed_a:
            feed_ab = _comparison_sheet(feed_a.copy(), feed_b, "Feed")
        with Image.open(story) as story_a:
            story_ab = _comparison_sheet(story_a.copy(), story_b, "Story")
        with Image.open(reel_frames[0]) as reel_a:
            reel_ab = _comparison_sheet(reel_a.copy(), reel_b, "Reel hook")
        with Image.open(tiktok_frames[0]) as tiktok_a:
            tiktok_ab = _comparison_sheet(tiktok_a.copy(), tiktok_b, "TikTok hook")

        ab_previews = (
            self._save(feed_ab, output / "ab-feed.png"),
            self._save(story_ab, output / "ab-story.png"),
            self._save(reel_ab, output / "ab-reel-hook.png"),
            self._save(tiktok_ab, output / "ab-tiktok-hook.png"),
        )

        executable = self.ffmpeg_path or shutil.which("ffmpeg")
        ffmpeg_ready = bool(executable and Path(executable).is_file())
        if ffmpeg_ready:
            reel_video_frames, reel_durations, reel_keyframe_root = self._animated_video_plan(
                output=output,
                prefix="instagram-reel",
                title=title,
                product=product,
                script=reel_script,
                duration_scale=duration_scale,
            )
            tiktok_video_frames, tiktok_durations, tiktok_keyframe_root = self._animated_video_plan(
                output=output,
                prefix="tiktok",
                title=title,
                product=product,
                script=tiktok_script,
                duration_scale=duration_scale,
            )
            try:
                reel_video = self._video(reel_video_frames, reel_durations, output / "instagram-reel.mp4")
                tiktok_video = self._video(tiktok_video_frames, tiktok_durations, output / "tiktok-draft.mp4")
            finally:
                shutil.rmtree(reel_keyframe_root, ignore_errors=True)
                shutil.rmtree(tiktok_keyframe_root, ignore_errors=True)
                keyframe_parent = output / ".video-keyframes"
                try:
                    keyframe_parent.rmdir()
                except OSError:
                    pass
        else:
            # Não renderiza dezenas de keyframes quando o vídeo nem pode ser
            # codificado. Mantém exatamente o contrato de erro anterior.
            reel_durations = tuple(
                max(0.1, (float(s["end"]) - float(s["start"])) * duration_scale)
                for s in reel_script
            )
            tiktok_durations = tuple(
                max(0.1, (float(s["end"]) - float(s["start"])) * duration_scale)
                for s in tiktok_script
            )
            reel_video = self._video(reel_frames, reel_durations, output / "instagram-reel.mp4")
            tiktok_video = self._video(tiktok_frames, tiktok_durations, output / "tiktok-draft.mp4")

        return CreativeAssets(
            root=output,
            feed=feed,
            story=story,
            reel_frames=reel_frames,
            reel_thumbnail=reel_thumbnail,
            reel_video=reel_video,
            tiktok_frames=tiktok_frames,
            tiktok_thumbnail=tiktok_thumbnail,
            tiktok_video=tiktok_video,
            ab_previews=ab_previews,
        )

