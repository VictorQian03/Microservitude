"""Abstract interfaces defining the boundaries of the core domain."""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
from decimal import Decimal
from typing import Dict, Iterable, Optional
from uuid import UUID

from .models import (
    CachedADV,
    CostRequestInput,
    CostRequestRecord,
    CostResult,
    ImpactModel,
    Liquidity,
    ModelName,
    RequestStatus,
)


class LiquidityRepository(ABC):
    """Read access to liquidity statistics for symbols."""

    @abstractmethod
    def get_liquidity(self, ticker: str, d: date) -> Optional[Liquidity]:
        """Return the liquidity snapshot for a ticker/date combination."""


class ModelRepository(ABC):
    """Lookup for active impact models and their parameters."""

    @abstractmethod
    def get_active_models(self) -> Iterable[ImpactModel]:
        """Return all active impact models ordered by preference."""

    @abstractmethod
    def get_latest_model(self, name: ModelName) -> Optional[ImpactModel]:
        """Return the most recent active model for a specific strategy."""


class CostRequestRepository(ABC):
    """Persistence boundary for cost estimation requests and results."""

    @abstractmethod
    def create_request(self, request: CostRequestRecord) -> None:
        """Persist a new cost request."""

    @abstractmethod
    def update_status(self, request_id: UUID, status: RequestStatus) -> None:
        """Update the lifecycle status for an existing request."""

    @abstractmethod
    def get_request(self, request_id: UUID) -> Optional[CostRequestRecord]:
        """Fetch a persisted request by identifier."""

    @abstractmethod
    def save_result(self, result: CostResult) -> None:
        """Persist the completed cost computation result."""

    @abstractmethod
    def get_result(self, request_id: UUID) -> Optional[CostResult]:
        """Retrieve a previously computed result if it exists."""


class CostEstimationQueue(ABC):
    """Queue boundary used to dispatch jobs to background workers."""

    @abstractmethod
    def enqueue(self, request: CostRequestRecord) -> str:
        """Push a new request onto the queue and return an identifier."""


class LiquidityCache(ABC):
    """Cache boundary to speed up repeated ADV lookups."""

    @abstractmethod
    def get_adv(self, ticker: str, d: date) -> Optional[CachedADV]:
        """Retrieve a cached ADV snapshot if available."""

    @abstractmethod
    def set_adv(self, payload: CachedADV, ttl_seconds: int | None = None) -> None:
        """Store an ADV snapshot with an optional TTL."""


class PriceService(ABC):
    """External service boundary for retrieving equity prices."""

    @abstractmethod
    def get_price(self, ticker: str, d: date) -> Optional[Decimal]:
        """Return a reference price for the ticker on a given date."""


class EstimationOrchestrator(ABC):
    """Application service boundary driving the estimation workflow."""

    @abstractmethod
    def submit(self, payload: CostRequestInput) -> UUID:
        """Validate, persist, and enqueue a new cost estimation request."""

    @abstractmethod
    def get_status(self, request_id: UUID) -> Dict[str, object]:
        """Return the status or final result for a request."""
