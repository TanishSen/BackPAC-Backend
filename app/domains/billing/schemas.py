"""The plan a person is on, as the app sees it."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class PlanOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    #: Whether Premium is on sale at all. False => the app shows no upgrade
    #: prompt and no allowance, because there is no limit for Premium to lift.
    billing_enabled: bool = Field(alias="billingEnabled")
    premium: bool
    premium_until: datetime | None = Field(default=None, alias="premiumUntil")
    will_renew: bool = Field(default=False, alias="willRenew")
    product_id: str | None = Field(default=None, alias="productId")
    #: Null when unlimited — Premium, or the allowance switched off.
    free_monthly_limit: int | None = Field(default=None, alias="freeMonthlyLimit")
    used_this_month: int = Field(default=0, alias="usedThisMonth")
    remaining_this_month: int | None = Field(default=None, alias="remainingThisMonth")
