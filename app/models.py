from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import Column, LargeBinary
from sqlmodel import SQLModel, Field


class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    username: str
    email: str = Field(index=True, unique=True)
    password: str
    role: str  # builder | provider | admin
    approved: bool = True  # providers start False until an admin approves


class WasteRequest(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    builder_id: int = Field(foreign_key="user.id", index=True)
    provider_id: Optional[int] = Field(default=None, foreign_key="user.id", index=True)
    tracking_id: Optional[str] = Field(default=None, index=True)
    completed_at: Optional[datetime] = None
    lat: Optional[float] = None  # optional map coordinates; the address field stays the primary location
    lng: Optional[float] = None
    waste_type: str
    tons: float
    location: str
    pickup_date: str = Field(index=True)
    status: str = Field(default="Open", index=True)


class StatusLog(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    request_id: int = Field(foreign_key="wasterequest.id")
    status: str
    at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    by: str


class Listing(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    owner_id: int = Field(foreign_key="user.id")
    material: str
    quantity: str
    location: str
    note: str = ""


class Notification(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    message: str
    type: str = "info"
    is_read: bool = Field(default=False, index=True)
    dedupe_key: str = Field(index=True, unique=True)  # one notification per event
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AuditLog(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: Optional[int] = Field(default=None, foreign_key="user.id")
    actor: str = ""
    action: str
    entity_type: str
    entity_id: Optional[int] = None
    detail: str = ""
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), index=True)


class WasteClassification(SQLModel, table=True):
    # Historical records from the retired AI feature. Kept (read-only) so old data is never lost.
    id: Optional[int] = Field(default=None, primary_key=True)
    request_id: int = Field(foreign_key="wasterequest.id", index=True)
    material: str
    confidence: Optional[float] = None
    recyclable: bool
    recommended_action: str
    possible_reuse: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class EnvironmentalFactor(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    material: str = Field(index=True, unique=True)
    factor: float
    unit: str = "tCO2e/tonne"
    source: str = ""  # empty = unverified placeholder
    last_updated: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class RequestPhoto(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    request_id: int = Field(foreign_key="wasterequest.id", index=True, unique=True)  # one photo per request
    content_type: str = "image/jpeg"
    size: int
    data: bytes = Field(sa_column=Column(LargeBinary, nullable=False))  # stored in the database: Render's web disk is ephemeral
    uploaded_by: int = Field(foreign_key="user.id")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
