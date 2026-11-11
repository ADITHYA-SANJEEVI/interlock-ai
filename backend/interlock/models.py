from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class ControlBase(DeclarativeBase):
    pass


class Tenant(ControlBase):
    __tablename__ = "tenants"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    buyer: Mapped[str] = mapped_column(String)
    merchant: Mapped[str] = mapped_column(String)
    seller: Mapped[str] = mapped_column(String, default="workspace-seller-v1")
    integration: Mapped[str] = mapped_column(String)
    account: Mapped[str] = mapped_column(String)


class Identity(ControlBase):
    __tablename__ = "identities"
    username: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    beneficiary: Mapped[str] = mapped_column(String)
    password_hash: Mapped[str] = mapped_column(String)
    role: Mapped[str] = mapped_column(String)


class Purchase(ControlBase):
    __tablename__ = "purchases"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        CheckConstraint("amount > 0 AND amount <= 1000000"),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    buyer: Mapped[str] = mapped_column(String)
    owner: Mapped[str] = mapped_column(ForeignKey("identities.username"), index=True)
    seller: Mapped[str] = mapped_column(String)
    provider: Mapped[str] = mapped_column(String)
    account: Mapped[str] = mapped_column(String)
    integration: Mapped[str] = mapped_column(String)
    quote: Mapped[dict] = mapped_column(JSONB)
    amount: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String, default="INR")
    request_digest: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PurchaseState(ControlBase):
    __tablename__ = "purchase_states"
    purchase_id: Mapped[str] = mapped_column(ForeignKey("purchases.id"), primary_key=True)
    seller_order: Mapped[str | None] = mapped_column(String)
    provider_order: Mapped[str | None] = mapped_column(String, unique=True)
    payment_id: Mapped[str | None] = mapped_column(String, unique=True)
    preparation: Mapped[str] = mapped_column(String, default="FROZEN")
    provider_status: Mapped[str] = mapped_column(String, default="NOT_OBSERVED")
    canonical_status: Mapped[str] = mapped_column(String, default="NOT_OBSERVED")
    entitlement: Mapped[str] = mapped_column(String, default="UNFULFILLED")
    exposed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    conflict: Mapped[bool] = mapped_column(Boolean, default=False)


class Operation(ControlBase):
    __tablename__ = "operations"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "purchase_id"], ["purchases.tenant_id", "purchases.id"]),
        UniqueConstraint("purchase_id", "kind", "operation_key"),
        CheckConstraint("amount > 0"),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String, index=True)
    purchase_id: Mapped[str] = mapped_column(String, index=True)
    kind: Mapped[str] = mapped_column(String)
    operation_key: Mapped[str] = mapped_column(String, unique=True)
    target: Mapped[str] = mapped_column(String)
    amount: Mapped[int] = mapped_column(BigInteger)
    payload: Mapped[dict] = mapped_column(JSONB)
    request_digest: Mapped[str] = mapped_column(String)


class OperationState(ControlBase):
    __tablename__ = "operation_states"
    operation_id: Mapped[str] = mapped_column(ForeignKey("operations.id"), primary_key=True)
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    knowledge: Mapped[str] = mapped_column(String, default="NOT_ATTEMPTED")
    resource_id: Mapped[str | None] = mapped_column(String, unique=True)


class Observation(ControlBase):
    __tablename__ = "observations"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "purchase_id"], ["purchases.tenant_id", "purchases.id"]),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String, index=True)
    purchase_id: Mapped[str] = mapped_column(String, index=True)
    source: Mapped[str] = mapped_column(String)
    provider: Mapped[str] = mapped_column(String)
    account: Mapped[str] = mapped_column(String)
    mode: Mapped[str] = mapped_column(String, default="TEST")
    payload: Mapped[dict] = mapped_column(JSONB)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Capture(ControlBase):
    __tablename__ = "captures"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "purchase_id"], ["purchases.tenant_id", "purchases.id"]),
        UniqueConstraint("purchase_id"),
        UniqueConstraint("provider", "account", "payment_id"),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String, index=True)
    purchase_id: Mapped[str] = mapped_column(String)
    provider: Mapped[str] = mapped_column(String)
    account: Mapped[str] = mapped_column(String)
    payment_id: Mapped[str] = mapped_column(String)
    amount: Mapped[int] = mapped_column(BigInteger)
    observation_id: Mapped[str] = mapped_column(ForeignKey("observations.id"), index=True)


class WebhookReceipt(ControlBase):
    __tablename__ = "webhook_receipts"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    body_hash: Mapped[str] = mapped_column(String)
    payload: Mapped[dict] = mapped_column(JSONB)
    conflict: Mapped[bool] = mapped_column(Boolean, default=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SellerBase(DeclarativeBase):
    pass


class SellerQuote(SellerBase):
    __tablename__ = "quotes"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String, index=True)
    body: Mapped[dict] = mapped_column(JSONB)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SellerOrder(SellerBase):
    __tablename__ = "orders"
    __table_args__ = (UniqueConstraint("tenant_id", "id"),)
    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String, index=True)
    quote_id: Mapped[str] = mapped_column(ForeignKey("quotes.id"), unique=True)
    purchase_id: Mapped[str] = mapped_column(String, unique=True)
    terms: Mapped[dict] = mapped_column(JSONB)
    revision: Mapped[int] = mapped_column(Integer, default=1)


class Seat(SellerBase):
    __tablename__ = "seats"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "order_id"], ["orders.tenant_id", "orders.id"]),
        UniqueConstraint("order_id", "beneficiary"),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String, index=True)
    order_id: Mapped[str] = mapped_column(String, index=True)
    beneficiary: Mapped[str] = mapped_column(String, index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    revision: Mapped[int] = mapped_column(Integer, default=1)


class SellerConsumption(SellerBase):
    __tablename__ = "consumptions"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String, index=True)
    body_digest: Mapped[str] = mapped_column(String)
    result: Mapped[dict] = mapped_column(JSONB)


class SellerUser(SellerBase):
    __tablename__ = "users"
    username: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String, index=True)
    beneficiary: Mapped[str] = mapped_column(String)
    token_hash: Mapped[str] = mapped_column(String)


class WorkspaceEntry(SellerBase):
    __tablename__ = "workspace_entries"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    seat_id: Mapped[str] = mapped_column(ForeignKey("seats.id"), index=True)
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SellerEvent(SellerBase):
    __tablename__ = "events"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String, index=True)
    kind: Mapped[str] = mapped_column(String)
    resource_id: Mapped[str] = mapped_column(String)
    detail: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ReferenceBase(DeclarativeBase):
    pass


class ReferenceResource(ReferenceBase):
    __tablename__ = "resources"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    kind: Mapped[str] = mapped_column(String, index=True)
    request_digest: Mapped[str] = mapped_column(String)
    body: Mapped[dict] = mapped_column(JSONB)


class ReferenceClock(ReferenceBase):
    __tablename__ = "clock"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tick: Mapped[int] = mapped_column(BigInteger, default=0)
