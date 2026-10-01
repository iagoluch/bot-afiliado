from __future__ import annotations

from abc import ABC, abstractmethod
from enum import StrEnum
from pathlib import Path

from app.models import Offer


class Capability(StrEnum):
    DISCOVER_PRODUCTS = "DISCOVER_PRODUCTS"
    DISCOVER_DEALS = "DISCOVER_DEALS"
    GET_PRICE = "GET_PRICE"
    GET_COUPONS = "GET_COUPONS"
    GET_COMMISSION = "GET_COMMISSION"
    CREATE_AFFILIATE_LINK = "CREATE_AFFILIATE_LINK"
    GET_CONVERSIONS = "GET_CONVERSIONS"
    GET_REPORTS = "GET_REPORTS"
    GET_CREATIVES = "GET_CREATIVES"


class CapabilityStatus(StrEnum):
    SUPPORTED = "SUPPORTED"
    MANUAL = "MANUAL"
    WAITING_FOR_CREDENTIALS = "WAITING_FOR_CREDENTIALS"
    MANUAL_OR_PENDING = "MANUAL_OR_PENDING"
    UNVERIFIED = "UNVERIFIED"
    WAITING_FOR_WRITTEN_APPROVAL_AND_RETENTION_DESIGN = "WAITING_FOR_WRITTEN_APPROVAL_AND_RETENTION_DESIGN"


class AffiliateAdapter(ABC):
    name: str
    integration_status: CapabilityStatus
    capabilities: dict[Capability, CapabilityStatus]

    @abstractmethod
    def import_offers(self, path: Path) -> list[Offer]:
        raise NotImplementedError
