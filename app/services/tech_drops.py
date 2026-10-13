"""Deterministic, product-aware, claim-free marketing copy for ML Telegram.

Keep styling/creative language separate from verified commercial facts.
The same offer always renders identically so the queue integrity check is stable.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Style:
    headlines: tuple[str, ...]
    blurbs: tuple[str, ...]
    relevance: str


STYLES: tuple[tuple[str, Style], ...] = (
    (r"\b(?:ryzen|processador|intel core|core i[3579]|cpu)\b", Style(
        ("⚙️ PROCESSADOR NO RADAR!", "🧠 HORA DE OLHAR O PROCESSADOR!"),
        ("Montando um PC? Vale colocar esse processador na comparação.",
         "Procurando uma CPU para o seu projeto? Dá uma olhada neste modelo."),
        r"\b(?:ryzen|cpu|processador|am4|intel)\b")),
    (r"\bgabinete\b.*\b(?:aqu[aá]rio|vidro)\b", Style(
        ("🧊 VISUAL AQUÁRIO NO SETUP!", "🖥️ GABINETE EM DESTAQUE!"),
        ("Curte gabinete com vidro e visual aquário? Confira este modelo.",
         "Quer comparar gabinetes com vidro para o setup? Olha essa opção."),
        r"\b(?:gabinete|aqu[aá]rio|vidro)\b")),
    (r"\bgabinete\b", Style(
        ("🖥️ GABINETE NO RADAR!", "🧰 HORA DE PENSAR NO GABINETE!"),
        ("Montando seu setup? Vale comparar esse gabinete.",
         "Procurando gabinete para o PC? Olha esse modelo."),
        r"\b(?:gabinete)\b")),
    (r"\b(?:ssd|nvme|m\.2)\b", Style(
        ("💾 SSD NO RADAR!", "💽 OLHO NO ARMAZENAMENTO!"),
        ("Procurando SSD para o seu PC? Confira esse modelo.",
         "Na lista de upgrades de armazenamento? Dá uma olhada nesse SSD."),
        r"\b(?:ssd|nvme|armazenamento)\b")),
    (r"\b(?:placa de v[ií]deo|gpu|geforce|radeon)\b", Style(
        ("🎮 PLACA DE VÍDEO NA MIRA!", "🖥️ GPU NO RADAR!"),
        ("Comparando placas de vídeo para o PC? Veja essa opção.",
         "De olho em GPU para o seu setup? Coloque esse modelo na lista."),
        r"\b(?:gpu|placa de v[ií]deo|geforce|radeon)\b")),
    (r"\b(?:notebook|laptop)\b", Style(
        ("💻 NOTEBOOK NA SELEÇÃO!", "💻 DE OLHO NOS NOTEBOOKS!"),
        ("Pesquisando notebook? Coloque esse modelo na comparação.",
         "Trabalho, estudo ou lazer: vale olhar as especificações desse notebook."),
        r"\b(?:notebook|laptop)\b")),
    (r"\b(?:celular|smartphone|iphone|galaxy)\b", Style(
        ("📱 CELULAR NO RADAR!", "📲 SMARTPHONE NA SELEÇÃO!"),
        ("Pensando em trocar de celular? Confira essa opção.",
         "Comparando smartphones? Dá uma olhada neste modelo."),
        r"\b(?:celular|smartphone|iphone|galaxy)\b")),
    (r"\b(?:tablet|ipad)\b", Style(
        ("📲 TABLET NO RADAR!", "📱 TABLET NA SELEÇÃO!"),
        ("Procurando tablet? Vale comparar esse modelo.",
         "De olho nos tablets? Confira esta opção."),
        r"\b(?:tablet|ipad)\b")),
    (r"\b(?:mem[oó]ria\s+ram|ddr[345]|ram)\b", Style(
        ("🧠 MEMÓRIA RAM NA LISTA!", "🛠️ UPGRADES NA MIRA!"),
        ("Pesquisando memória RAM para seu PC? Confira este modelo.",
         "Montando a configuração do computador? Veja esta opção de RAM."),
        r"\b(?:ram|mem[oó]ria|ddr)\b")),
    (r"\bmonitor\b", Style(
        ("🖥️ MONITOR NO RADAR!", "👀 DE OLHO NA TELA!"),
        ("Comparando monitores para seu setup? Confira essa tela.",
         "Procurando monitor novo? Vale conferir as especificações deste."),
        r"\b(?:monitor|tela|display)\b")),
    (r"\b(?:mouse)\b", Style(
        ("🖱️ MOUSE NA SELEÇÃO!", "🖱️ PERIFÉRICO NO RADAR!"),
        ("De olho num mouse para o setup? Confira esse modelo.",
         "Comparando mouses? Vale colocar este na lista."),
        r"\b(?:mouse|mouses|perif[eé]rico)\b")),
    (r"\b(?:teclado)\b", Style(
        ("⌨️ TECLADO NO RADAR!", "⌨️ PERIFÉRICO NA MIRA!"),
        ("Procurando teclado para o PC? Confira esse modelo.",
         "De olho nos teclados para o setup? Veja essa opção."),
        r"\b(?:teclado|digita[cç][aã]o)\b")),
    (r"\b(?:fone|headset|earbuds|bluetooth\s+headphone)\b", Style(
        ("🎧 ÁUDIO NA SELEÇÃO!", "🎵 FONE NO RADAR!"),
        ("Comparando fones de ouvido? Confira esse modelo.",
         "Na busca por headset ou fone? Olha essa opção de áudio."),
        r"\b(?:fone|headset|[aá]udio|ouvido)\b")),
    (r"\b(?:carregador|fonte\s+usb)\b", Style(
        ("🔌 ACESSÓRIO NO RADAR!", "⚡ CARREGADOR NA SELEÇÃO!"),
        ("Procurando carregador? Confira esta opção.",
         "Comparando carregadores? Veja este modelo."),
        r"\b(?:carregador|carga)\b")),
    (r"\b(?:placa.m[aã]e|motherboard)\b", Style(
        ("🛠️ PLACA-MÃE NA LISTA!", "🧩 MONTAGEM DE PC NO RADAR!"),
        ("Montando um PC? Confira essa placa-mãe.",
         "Pesquisando placa-mãe para seu projeto? Veja esse modelo."),
        r"\b(?:placa.m[aã]e|motherboard)\b")),
    (r"\b(?:pendrive|pen drive)\b", Style(
        ("💾 ARMAZENAMENTO NO RADAR!", "🔌 PENDRIVE NA LISTA!"),
        ("Comparando pendrives? Confira esta opção.",
         "Procurando pendrive? Vale ver este modelo."),
        r"\b(?:pendrive|pen drive|armazenamento)\b")),
    (r"\b(?:impressora)\b", Style(
        ("🖨️ IMPRESSORA NA LISTA!", "🖨️ DE OLHO NA IMPRESSÃO!"),
        ("Procurando impressora? Confira as características deste modelo.",
         "Comparando impressoras? Vale olhar esta opção."),
        r"\b(?:impressora|impress[aã]o)\b")),
    (r"\b(?:webcam|c[aâ]mera)\b", Style(
        ("📷 CÂMERA NO RADAR!", "📹 WEBCAM NA SELEÇÃO!"),
        ("Pesquisando webcam ou câmera? Veja esta opção.",
         "Comparando câmeras para o setup? Confira esse modelo."),
        r"\b(?:c[aâ]mera|webcam|v[ií]deo)\b")),
    (r"\b(?:microfone)\b", Style(
        ("🎙️ MICROFONE NA LISTA!", "🎤 ÁUDIO NO RADAR!"),
        ("Procurando microfone para seu setup? Olha este modelo.",
         "Comparando microfones? Confira esta opção."),
        r"\b(?:microfone)\b")),
    (r"\b(?:smartwatch|rel[oó]gio\s+inteligente)\b", Style(
        ("⌚ WEARABLE NA SELEÇÃO!", "⌚ RELÓGIO NO RADAR!"),
        ("Pesquisando smartwatch? Confira esse modelo.",
         "Comparando relógios inteligentes? Olha esta opção."),
        r"\b(?:smartwatch|rel[oó]gio|wearable)\b")),
    (r"\b(?:roteador|repetidor|wi-?fi|switch\s+gigabit)\b", Style(
        ("📶 REDE NO RADAR!", "🌐 CONEXÃO NA LISTA!"),
        ("Montando ou organizando a rede? Veja este equipamento.",
         "Comparando equipamentos de rede? Confira este modelo."),
        r"\b(?:rede|roteador|wi-?fi|conex[aã]o)\b")),
)

DEFAULT = Style(
    ("🔎 TECH DROP NA ÁREA!", "📦 TECNOLOGIA NA SELEÇÃO!"),
    ("Mais um produto para entrar na sua lista de comparação.",
     "Na busca por tecnologia? Confira os detalhes deste produto."),
    r"$^",
)

# Explicit seller codes are not useful in the Telegram headline. Do not
# remove technical specs, brands, model names, colors or compatibility terms.
_SELLER_CODE = re.compile(r"\b(?:100-\d{7,}[A-Z0-9]*|MLB-?\d{8,})\b", re.I)
_UNTRUSTED_MARKUP = re.compile(r"[\x00-\x1f\x7f]+")


def clean_title(raw: str, max_length: int = 106) -> str:
    value = " ".join(_SELLER_CODE.sub("", _UNTRUSTED_MARKUP.sub(" ", raw)).split())
    value = value.strip(" -|,;")
    if len(value) > max_length:
        value = value[: max_length + 1].rsplit(" ", 1)[0].strip(" -|,;") + "…"
    return value or "Produto de tecnologia"


def _style(title: str) -> Style:
    return next((style for pattern, style in STYLES if re.search(pattern, title, re.I)), DEFAULT)


def _choice(options: tuple[str, ...], identity: str, salt: str) -> str:
    digest = hashlib.sha256((salt + ":" + identity).encode("utf-8")).digest()
    return options[digest[0] % len(options)]


def creative_copy(title: str, identity: str, ai_hook: str | None = None) -> tuple[str, str]:
    """Stable selection; require the AI line to actually relate to the product."""
    style = _style(title)
    heading = _choice(style.headlines, identity, "heading")
    generic = bool(ai_hook and re.search(
        r"\b(?:mais uma op[cç][aã]o|op[cç][aã]o de tecnologia|estilo [uú]nico|"
        r"produto imperd[ií]vel|confira esta oferta)\b", ai_hook, re.I
    ))
    hook = ai_hook if (ai_hook and not generic
                       and re.search(style.relevance, ai_hook, re.I)) else None
    return heading, hook or _choice(style.blurbs, identity, "body")
