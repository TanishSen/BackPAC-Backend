"""The profile screen's shapes: who you are, what you have done, your plan."""

from pydantic import BaseModel, ConfigDict, Field

from app.domains.billing.schemas import PlanOut


class ProfileOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True, from_attributes=True)

    #: Null until set in "Edit profile"; the app falls back to the sign-up name.
    display_name: str | None = Field(default=None, alias="displayName")
    home_city: str | None = Field(default=None, alias="homeCity")
    bio: str | None = None
    avatar: str | None = None


class ProfileUpdate(BaseModel):
    """Body for `PATCH /me`. Send only what changed; an empty string clears a
    field back to unset."""

    model_config = ConfigDict(populate_by_name=True)

    display_name: str | None = Field(default=None, alias="displayName", max_length=60)
    home_city: str | None = Field(default=None, alias="homeCity", max_length=80)
    bio: str | None = Field(default=None, max_length=160)
    avatar: str | None = Field(default=None, max_length=16)


class StatsOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    #: Conversations anyone spoke in.
    trips: int = 0
    #: Distinct destinations the agent searched for.
    places: int = 0
    saved: int = 0
    favourites: int = 0
    #: Marked "trip taken".
    completed: int = 0
    #: Still being planned.
    in_progress: int = Field(default=0, alias="inProgress")
    #: Places on the bucket list (saved trips).
    bucket_list: int = Field(default=0, alias="bucketList")


class MeOut(BaseModel):
    """Everything the profile screen draws, in one request."""

    model_config = ConfigDict(populate_by_name=True)

    profile: ProfileOut
    stats: StatsOut
    plan: PlanOut
