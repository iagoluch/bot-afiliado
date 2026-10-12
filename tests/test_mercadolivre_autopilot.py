from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.config import Settings
from app.db import Database
from app.models import utc_now
from app.mercadolivre_autopilot import (
    MLError, _money_from_visible, _queue_validated_product,
    affiliate_from_text, normalize_product_url,
)
from app.services.pipeline import Pipeline


@pytest.mark.parametrize("url,expected", [
    (
        "https://www.mercadolivre.com.br/fone-dapon/p/MLB24117280?tracking_id=secret#pos=0",
        "https://www.mercadolivre.com.br/fone-dapon/p/MLB24117280",
    ),
    ("https://www.mercadolivre.com.br/p/MLB24117280","https://www.mercadolivre.com.br/p/MLB24117280"),
    ("https://produto.mercadolivre.com.br/MLB-4587377807-headphone-_JM",
     "https://produto.mercadolivre.com.br/MLB-4587377807-headphone-_JM"),
    ("http://www.mercadolivre.com.br/p/MLB24117280",None),
    ("https://mercadolivre.com.br.evil.example/p/MLB24117280",None),
    ("https://mercadolivre.com.br@evil.example/p/MLB24117280",None),
    ("https://evil.example/mlb24117280",None),
    ("https://www.mercadolivre.com.br/l/cupom",None),
])
def test_sources_canonicalized_and_other_hosts_blocked(url,expected):
    assert normalize_product_url(url)==expected


def test_extracts_one_real_official_affiliate_link_not_plain_product():
    assert affiliate_from_text("https://meli.la/2MWiFPG")=="https://meli.la/2MWiFPG"
    assert affiliate_from_text("https://meli.la/2MWiFPG\nhttps://meli.la/2MWiFPG")=="https://meli.la/2MWiFPG"
    assert affiliate_from_text("https://meli.la/AAA\nhttps://meli.la/BBB") is None
    assert affiliate_from_text("https://www.mercadolivre.com.br/p/MLB24117280") is None
    assert affiliate_from_text("https://meli.la.evil.test/AAA") is None
    assert affiliate_from_text("https://evil.test/?x=https://meli.la/AAA") is None


@pytest.mark.parametrize("text,expected", [
    ("74,90",7490),
    ("1.234,90",123490),
    ("74,9",None),
    ("R$ 74,90",None),
    ("",None),
    ("grátis",None),
    ("1e10000",None),
])
def test_visible_price_parsing(text,expected):
    assert _money_from_visible(text)==expected


def _settings(tmp_path,*, dry_run=False,telegram_only=True):
    return Settings(
        database_path=tmp_path / "ml-browser.db",
        dry_run=dry_run,
        public_base_url="http://127.0.0.1:8000",
        telegram_bot_token="mock",
        telegram_chat_id="@publicchannel",
        channel_visibility={"telegram":"public"},
        telegram_only_mode=telegram_only,
        telegram_real_enabled=not dry_run,
    )


def _details(*, timestamp=None, price=7490, title="Headphone Dapon H02D Bluetooth"):
    return {
        "external_id":"MLB24117280",
        "source_url":"https://www.mercadolivre.com.br/p/MLB24117280",
        "title":title,"price_cents":price,
        "stock_status":"IN_STOCK",
        "confirmed_at":timestamp or utc_now(),
    }


def test_autopilot_validated_offer_queues_job_for_real_worker(tmp_path):
    settings=_settings(tmp_path)
    db=Database(settings.database_path)
    db.init()
    result=_queue_validated_product(db,settings,_details(),"https://meli.la/2MWiFPG")
    assert result["status"]=="QUEUED_FOR_WORKER"
    assert result["published_now"]==0
    assert db.rows("SELECT COUNT(*) n FROM content_jobs WHERE dry_run=0")[0]["n"]==1
    assert db.rows("SELECT COUNT(*) n FROM publications")[0]["n"]==0
    row=db.get_offer(result["offer_ids"][0])
    assert row["current_price_cents"]==7490
    assert row["source_type"]=="ML_BROWSER_VERIFIED"
    assert row["affiliate_url"]=="https://meli.la/2MWiFPG"
    pipe=Pipeline(db,settings)
    assert len(pipe.curate_and_queue(result["offer_ids"],"browser-real"))==1
    assert db.rows("SELECT COUNT(*) n FROM publications")[0]["n"]==0


def test_autopilot_never_queues_invalid_link(tmp_path):
    settings=_settings(tmp_path)
    db=Database(settings.database_path)
    db.init()
    with pytest.raises(MLError,match="LINK_NAO_AUTORIZADO"):
        _queue_validated_product(db,settings,_details(),"https://www.mercadolivre.com.br/p/MLB24117280")
    assert not db.rows("SELECT 1 FROM offers")


def test_autopilot_never_queues_old_price(tmp_path):
    settings=_settings(tmp_path)
    db=Database(settings.database_path)
    db.init()
    old=(datetime.now(timezone.utc)-timedelta(minutes=10)).isoformat(timespec="seconds")
    with pytest.raises(MLError,match="EXPIRADA"):
        _queue_validated_product(db,settings,_details(timestamp=old),"https://meli.la/2MWiFPG")
    assert not db.rows("SELECT 1 FROM offers")


def test_autopilot_telegram_only_gate(tmp_path):
    settings=_settings(tmp_path,telegram_only=False)
    db=Database(settings.database_path)
    db.init()
    with pytest.raises(MLError,match="NAO_AUTORIZADO"):
        _queue_validated_product(db,settings,_details(),"https://meli.la/2MWiFPG")
    assert not db.rows("SELECT 1 FROM offers")


def test_autopilot_final_dispatch_rejects_expired_verification(tmp_path):
    settings=_settings(tmp_path)
    db=Database(settings.database_path)
    db.init()
    result=_queue_validated_product(db,settings,_details(),"https://meli.la/2MWiFPG")
    oid=result["offer_ids"][0]
    pipe=Pipeline(db,settings)
    assert pipe.curate_and_queue([oid],"browser-expiry")
    with db.connect() as con:
        con.execute("UPDATE offers SET expires_at=? WHERE id=?",
                    ((datetime.now(timezone.utc)-timedelta(seconds=10)).isoformat(timespec="seconds"),oid))
    result=pipe.process_one()
    assert result["status"]=="REJECTED_STALE"
    assert db.rows("SELECT COUNT(*) n FROM publications")[0]["n"]==0


def test_nontech_product_never_queues(tmp_path):
    settings=_settings(tmp_path)
    db=Database(settings.database_path)
    db.init()
    with pytest.raises(MLError,match="FORA_DO_NICHO"):
        _queue_validated_product(db,settings,_details(title="Conjunto de panelas de inox"),
                                 "https://meli.la/2MWiFPG")



def test_generator_ui_accepts_one_link_generated_for_selected_telegram_tag():
    from app.mercadolivre_autopilot import _generate_affiliate_link
    import re

    class Element:
        def __init__(self, *, text="", href="", count=1):
            self.text, self.href, self._count = text, href, count
            self.first = self
            self.value = ""
            self.clicked = False
        def count(self):
            return self._count
        def is_visible(self, timeout=None):
            return True
        def fill(self, value):
            self.value = value
        def click(self, timeout=None):
            self.clicked = True
        def get_attribute(self, name):
            return self.href
        def nth(self, index):
            return self
        def inner_text(self, timeout=None):
            return self.text
        def input_value(self):
            return self.value
        def wait_for(self, timeout=None):
            return None

    class Page:
        url = "https://www.mercadolivre.com.br/affiliate-program/gerador"
        def __init__(self):
            self.input = Element()
            self.button = Element()
            self.link = Element(href="https://meli.la/2MWiFPG")
        def goto(self, url, **kwargs):
            self.url = url
        def locator(self, selector):
            if selector == "textarea":
                return self.input
            if selector.startswith("a[href"):
                return self.link
            return Element(count=0)
        def get_by_role(self, role, name):
            return self.button
        def get_by_text(self, text, exact=False):
            return Element()
        def wait_for_timeout(self, ms):
            return None

    page=Page()
    link=_generate_affiliate_link(page,page.url,"https://www.mercadolivre.com.br/p/MLB24117280")
    assert link=="https://meli.la/2MWiFPG"
    assert page.input.value=="https://www.mercadolivre.com.br/p/MLB24117280"
    assert page.button.clicked


def test_generator_refuses_if_telegram_label_missing():
    from app.mercadolivre_autopilot import _generate_affiliate_link

    class Element:
        first = None
        def __init__(self,found=True):
            self.first=self
            self.found=found
        def count(self):
            return 1 if self.found else 0
        def is_visible(self,timeout=None):
            return False

    class Page:
        url="https://www.mercadolivre.com.br/affiliate-program/generator"
        def goto(self,url,**kwargs):
            self.url=url
        def locator(self,selector):
            return Element()
        def get_by_role(self,role,name):
            return Element()
        def get_by_text(self,text,exact=False):
            return Element(found=False)

    with pytest.raises(MLError,match="ETIQUETA_TELEGRAM_NAO_SELECIONADA"):
        _generate_affiliate_link(Page(),"https://www.mercadolivre.com.br/affiliate-program/generator",
                                 "https://www.mercadolivre.com.br/p/MLB24117280")
