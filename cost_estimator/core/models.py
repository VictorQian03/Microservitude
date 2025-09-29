"""Domain data models for the execution cost estimator core."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Dict, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field

Side = Literal["buy", "sell"]
RequestStatus = Literal["queued", "done", "error"]
ModelName = Literal["pct_adv", "sqrt"]


class CostRequestInput(BaseModel):
    """Incoming request payload for a cost estimation."""

    ticker: str = Field(..., min_length=1)
    shares: int = Field(..., gt=0, description="Number of shares to trade")
    side: Side
    d: date = Field(..., alias="date")

    model_config = {"populate_by_name": True}


class Liquidity(BaseModel):
    """Liquidity snapshot for a ticker on a specific trading day."""

    ticker: str
    d: date
    adv_usd: Decimal = Field(..., gt=0, description="Average daily dollar volume")


class ImpactModel(BaseModel):
    """Model metadata and parameters loaded from the repository."""

    name: ModelName
    version: int = Field(..., ge=1)
    params: Dict[str, Decimal]
    active: bool = True
    created_at: Optional[datetime] = None


class CostRequestRecord(BaseModel):
    """Persisted representation of a cost estimation request."""

    id: UUID
    ticker: str
    shares: int = Field(..., gt=0)
    side: Side
    d: date
    notional_usd: Decimal = Field(..., gt=0)
    status: RequestStatus
    created_at: datetime


class ModelCostBreakdown(BaseModel):
    """Cost outcome produced by an individual model variant."""

    name: ModelName
    version: int
    parameters: Dict[str, Decimal]
    cost_usd: Decimal
    cost_bps: Decimal


class CostResult(BaseModel):
    """Aggregated results for a completed cost estimation request."""

    request_id: UUID
    adv_usd: Decimal
    models: Dict[ModelName, ModelCostBreakdown]
    best_model: ModelName
    total_cost_usd: Decimal
    total_cost_bps: Decimal
    computed_at: datetime


class CachedADV(BaseModel):
    """Cached ADV lookup payload used by the cache adapter."""

    ticker: str
    d: date
    adv_usd: Decimal
    cached_at: datetime = Field(default_factory=datetime.utcnow)


class CostComputationInput(BaseModel):
    """Normalized payload for triggering a cost computation job."""

    request: CostRequestRecord
    liquidity: Liquidity
    impact_models: Dict[ModelName, ImpactModel]

    @property
    def notional(self) -> Decimal:
        """Convenience accessor for the notional side of the request."""

        return self.request.notional_usd
