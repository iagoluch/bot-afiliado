"""Product interpretation from explicitly advertised title text.

This is NOT a claim-verification engine and does not infer compatibility,
quality, shipping, payment terms or specifications absent from the title.
The first *product noun* (before a compatibility clause) determines the type,
not the most frequent keyword or a reference to another device.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Category:
    code: str
    label: str
    emoji: str
    terms: tuple[str, ...]
    aliases: tuple[str, ...]


@dataclass(frozen=True)
class ProductProfile:
    category: Category
    features: tuple[str, ...]
    confidence: str
    title: str

    @property
    def product_type(self) -> str:
        return self.category.label


# Single taxonomy for all products; no brand-specific rule or ad-hoc
# exceptions for one Mercado Livre listing.
CATEGORIES: tuple[Category, ...] = (
    Category("headphones", "fone de ouvido", "🎧",
             (r"\bfones?\b", r"\bheadphones?\b", r"\bheadsets?\b", r"\bearbuds?\b"),
             ("fone", "headphone", "headset", "áudio", "ouvido")),
    Category("charger", "carregador", "⚡",
             (r"\bcarregadores?\b", r"\bfonte\s+(?:usb|carregadora?)\b"),
             ("carregador", "carga")),
    Category("cable", "cabo", "🔌",
             (r"\bcabos?\b", r"\bcord[aã]o\s+(?:de\s+)?(?:dados|usb)\b"),
             ("cabo", "conexão")),
    Category("case", "capa", "🛡️",
             (r"\bcapinhas?\b", r"\bcapas?\b", r"\bcase\s+(?:para|protetora)\b"),
             ("capa", "capinha", "proteção")),
    Category("film", "película", "🛡️",
             (r"\bpel[ií]culas?\b", r"\bprotetor(?:a)?\s+de\s+tela\b"),
             ("película", "tela", "proteção")),
    Category("adapter", "adaptador", "🔌",
             (r"\badaptadores?\b", r"\bhub\s+usb\b"),
             ("adaptador", "conector", "hub")),
    Category("stand", "suporte", "🧰",
             (r"\bsuportes?\b",),
             ("suporte", "organização")),
    Category("powerbank", "bateria externa", "🔋",
             (r"\bpower\s*bank\b", r"\bbateria\s+externa\b"),
             ("bateria", "power bank")),
    Category("ssd", "SSD", "💾",
             (r"\bssd\b", r"\bnvme\b", r"\b(?:m[.]2)\s+ssd\b"),
             ("ssd", "armazenamento", "nvme")),
    Category("hdd", "HD", "💾",
             (r"\bhard\s*disk\b", r"\b(?:hd|hdd)\s+(?:interno|externo|sata|usb|de\s+\d)\b"),
             ("hd", "hdd", "armazenamento")),
    Category("memory", "memória RAM", "🧠",
             (r"\bmem[oó]ria\s+ram\b", r"\bram\b", r"\bddr[345]\b"),
             ("memória", "ram", "ddr")),
    Category("motherboard", "placa-mãe", "🧩",
             (r"\bplaca[\s-]m[aã]e\b", r"\bmotherboard\b"),
             ("placa-mãe", "placa mãe", "motherboard")),
    Category("graphics", "placa de vídeo", "🎮",
             (r"\bplaca\s+de\s+v[ií]deo\b", r"\b(?:geforce|radeon|rtx\s+\d|gtx\s+\d)\b"),
             ("placa de vídeo", "gpu", "geforce", "radeon")),
    Category("cpu", "processador", "⚙️",
             (r"\bprocessador(?:es)?\b", r"\bryzen\b", r"\bintel\s+core\b", r"\bcore\s+i[3579]\b"),
             ("processador", "ryzen", "cpu")),
    Category("psu", "fonte de alimentação", "🔋",
             (r"\bfonte\s+(?:atx|de\s+alimenta[cç][aã]o|[3456789]\d{2}\s*w)\b",),
             ("fonte", "energia")),
    Category("pc_case", "gabinete", "🖥️",
             (r"\bgabinetes?\b",),
             ("gabinete", "aquário")),
    Category("monitor", "monitor", "🖥️",
             (r"\bmonitores?\b",),
             ("monitor", "tela")),
    Category("webcam", "webcam", "📷",
             (r"\bwebcam\b", r"\bc[aâ]mera\s+(?:web|para\s+pc)\b"),
             ("webcam", "câmera")),
    Category("microphone", "microfone", "🎙️",
             (r"\bmicrofones?\b",),
             ("microfone", "áudio")),
    Category("speaker", "caixa de som", "🔊",
             (r"\bcaixa\s+de\s+som\b", r"\bsoundbar\b", r"\balto[\s-]falantes?\b"),
             ("caixa de som", "áudio", "soundbar")),
    Category("keyboard", "teclado", "⌨️",
             (r"\bteclados?\b",),
             ("teclado", "digitação")),
    Category("mouse", "mouse", "🖱️",
             (r"\bmouse\b", r"\bmouses\b"),
             ("mouse", "periférico")),
    Category("tablet", "tablet", "📲",
             (r"\btablets?\b", r"\bipad\b"),
             ("tablet", "ipad")),
    Category("notebook", "notebook", "💻",
             (r"\bnotebooks?\b", r"\blaptops?\b", r"\bmacbook\b"),
             ("notebook", "laptop")),
    Category("phone", "celular", "📱",
             (r"\bcelulares?\b", r"\bsmartphones?\b", r"\biphone\b", r"\bgalaxy\s+[a-zs]\d{1,3}\b"),
             ("celular", "smartphone", "iphone")),
    Category("watch", "smartwatch", "⌚",
             (r"\bsmartwatch(?:es)?\b", r"\brel[oó]gio\s+inteligente\b"),
             ("smartwatch", "relógio")),
    Category("router", "roteador", "📶",
             (r"\broteadores?\b", r"\brepetidor(?:es)?\s+wi[\s-]?fi\b", r"\baccess\s+point\b"),
             ("roteador", "rede", "wi-fi")),
    Category("printer", "impressora", "🖨️",
             (r"\bimpressoras?\b",),
             ("impressora", "impressão")),
    Category("controller", "controle", "🎮",
             (r"\bcontroles?\b", r"\bjoysticks?\b", r"\bgamepads?\b"),
             ("controle", "joystick", "gamepad")),
    Category("console", "console", "🎮",
             (r"\bconsoles?\b", r"\bplaystation\b", r"\bxbox\b"),
             ("console", "videogame")),
    Category("pendrive", "pendrive", "💾",
             (r"\bpendrives?\b", r"\bpen[\s-]?drive\b"),
             ("pendrive", "armazenamento")),
    Category("kit", "kit de acessórios", "🧰",
             (r"\bkit\s+(?:gamer|teclado|perif[eé]ricos)\b", r"\bcombo\s+(?:gamer|teclado|mouse)\b"),
             ("kit", "combo", "acessórios")),
)
UNKNOWN = Category("unknown", "produto de tecnologia", "🔎", (), ())

# Product titles frequently include device compatibility. A device appearing
# after this separator is NOT the product sold.
_COMPATIBILITY = re.compile(
    r"\b(?:compat[ií]vel\s+com|indicado\s+para|feito\s+para|"
    r"para\s+(?:o\s+|a\s+|os\s+|as\s+)?(?:iphone|ipad|galaxy|"
    r"celular|smartphone|notebook|tablet|monitor|pc|playstation|xbox))\b",
    re.I,
)
# Whitelist of neutral, literal technical tokens. Each displayed token must
# occur in the seller's advertised title. Nothing is generated numerically.
_FEATURE_PATTERNS = (
    re.compile(r"\b(?:tws|anc|ipx[0-9]|nvme|m[.]2|amoled|oled|ips|full\s*hd|qhd|4k|gan)\b", re.I),
    re.compile(r"\b(?:usb[\s-]?c|tipo\s+c|lightning|bluetooth\s*\d(?:[.,]\d)?|wi[\s-]?fi\s*[4567]|ddr[345])\b", re.I),
    re.compile(r"\b\d{1,4}(?:[.,]\d+)?\s*(?:hz|w|mah)\b", re.I),
    re.compile(r"\b\d{1,4}\s*(?:gb|tb)\b(?:\s+ram)?", re.I),
    re.compile(r"\b\d{1,2}(?:[.,]\d+)?\s*(?:pol(?:egadas)?|[\"”])", re.I),
)


def interpret_product(title: str) -> ProductProfile:
    raw = " ".join(str(title).split())[:250]
    match = _COMPATIBILITY.search(raw)
    product_clause = raw[:match.start()] if match else raw
    candidates: list[tuple[int, int, int, Category]] = []
    for order, cat in enumerate(CATEGORIES):
        for pattern in cat.terms:
            found = re.search(pattern, product_clause, re.I)
            if found:
                # Earliest product noun wins; prefer longer compound nouns
                # at the same position. Never classify using the compatibility
                # clause if the product itself is unknown.
                candidates.append((found.start(), -(found.end()-found.start()), order, cat))
    category = min(candidates, key=lambda x: x[:3])[3] if candidates else UNKNOWN

    features: list[tuple[int, str]] = []
    for pattern in _FEATURE_PATTERNS:
        for found in pattern.finditer(raw):
            value = found.group(0).strip()
            if value and not any(value.casefold() == prev.casefold() for _, prev in features):
                features.append((found.start(), value))
    features.sort(key=lambda item: item[0])
    return ProductProfile(
        category=category,
        features=tuple(text for _, text in features[:3]),
        confidence="TITLE_MATCH" if category is not UNKNOWN else "UNKNOWN",
        title=raw,
    )
