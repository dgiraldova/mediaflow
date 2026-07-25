from __future__ import annotations

import logging
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from enum import StrEnum
from typing import Generator

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import DateTime, ForeignKey, Integer, String, create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="MEDIAFLOW_")

    database_url: str = "sqlite:///./mediaflow-demo.db"
    internal_worker_token: str = "change-me-before-sharing"


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class OrganizationMember(Base):
    __tablename__ = "organization_members"

    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    media_type: Mapped[str] = mapped_column(String(20), nullable=False)
    upload_key: Mapped[str] = mapped_column(String(500), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="queued")
    byte_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ProcessingJob(Base):
    __tablename__ = "processing_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"), index=True)
    stage: Mapped[str] = mapped_column(String(50), nullable=False, default="queued")
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="queued")
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Role(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"
    VIEWER = "viewer"


class OrganizationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    slug: str = Field(pattern=r"^[a-z0-9-]+$", min_length=2, max_length=120)


class OrganizationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    slug: str


class MembershipOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    organization_id: str
    user_id: str
    role: str


class UploadInitiate(BaseModel):
    organization_id: str
    original_filename: str = Field(min_length=1, max_length=255)
    media_type: str = Field(pattern=r"^(video|image|audio)$")


class UploadInitiated(BaseModel):
    asset_id: str
    upload_id: str
    upload_key: str
    status: str


class AssetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    organization_id: str
    original_filename: str
    media_type: str
    upload_key: str
    status: str
    byte_size: int | None
    duration_ms: int | None
    width: int | None
    height: int | None
    error_message: str | None


class ProcessingJobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    asset_id: str
    stage: str
    status: str
    progress: int
    error_message: str | None


class ProcessingUpdate(BaseModel):
    stage: str = Field(min_length=1, max_length=50)
    status: str = Field(pattern=r"^(queued|processing|completed|failed)$")
    progress: int = Field(ge=0, le=100)
    byte_size: int | None = Field(default=None, ge=0)
    duration_ms: int | None = Field(default=None, ge=0)
    width: int | None = Field(default=None, ge=0)
    height: int | None = Field(default=None, ge=0)
    error_message: str | None = Field(default=None, max_length=500)


def make_engine(database_url: str) -> Engine:
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    return create_engine(database_url, connect_args=connect_args)


def create_app(database_url: str | None = None, internal_worker_token: str | None = None) -> FastAPI:
    settings = Settings()
    engine = make_engine(database_url or settings.database_url)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    token = internal_worker_token or settings.internal_worker_token

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        Base.metadata.create_all(engine)
        with session_factory() as db:
            seed_demo_data(db)
        yield

    app = FastAPI(title="Mediaflow Demo API", version="0.1.0", lifespan=lifespan)
    app.state.session_factory = session_factory

    @app.middleware("http")
    async def request_id(request: Request, call_next):
        request.state.request_id = request.headers.get("X-Request-Id", str(uuid.uuid4()))
        response = await call_next(request)
        response.headers["X-Request-Id"] = request.state.request_id
        return response

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        return error_response(request, exc.status_code, str(exc.detail), error_code(exc.status_code))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, _: RequestValidationError):
        return error_response(request, 422, "Request validation failed", "validation_error")

    def get_db() -> Generator[Session, None, None]:
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    def current_user(x_user_id: str | None = Header(default=None)) -> str:
        if not x_user_id:
            raise HTTPException(status_code=401, detail="X-User-Id header is required")
        return x_user_id

    def require_internal_token(x_internal_token: str | None = Header(default=None)) -> None:
        if not x_internal_token or x_internal_token != token:
            raise HTTPException(status_code=401, detail="Invalid internal worker token")

    def require_membership(organization_id: str, user_id: str, db: Session) -> OrganizationMember:
        membership = db.get(OrganizationMember, {"organization_id": organization_id, "user_id": user_id})
        if not membership:
            raise HTTPException(status_code=403, detail="You do not have access to this organization")
        return membership

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/v1/organizations", response_model=OrganizationOut, status_code=status.HTTP_201_CREATED)
    def create_organization(payload: OrganizationCreate, user_id: str = Depends(current_user), db: Session = Depends(get_db)) -> Organization:
        if db.scalar(select(Organization).where(Organization.slug == payload.slug)):
            raise HTTPException(status_code=409, detail="Organization slug already exists")
        organization = Organization(name=payload.name, slug=payload.slug)
        db.add(organization)
        db.flush()
        db.add(OrganizationMember(organization_id=organization.id, user_id=user_id, role=Role.OWNER))
        db.commit()
        db.refresh(organization)
        return organization

    @app.get("/api/v1/organizations", response_model=list[OrganizationOut])
    def list_organizations(user_id: str = Depends(current_user), db: Session = Depends(get_db)) -> list[Organization]:
        return list(db.scalars(select(Organization).join(OrganizationMember).where(OrganizationMember.user_id == user_id).order_by(Organization.name)))

    @app.get("/api/v1/organizations/{organization_id}/members", response_model=list[MembershipOut])
    def list_members(organization_id: str, user_id: str = Depends(current_user), db: Session = Depends(get_db)) -> list[OrganizationMember]:
        require_membership(organization_id, user_id, db)
        return list(db.scalars(select(OrganizationMember).where(OrganizationMember.organization_id == organization_id)))

    @app.post("/api/v1/uploads/initiate", response_model=UploadInitiated, status_code=status.HTTP_201_CREATED)
    def initiate_upload(payload: UploadInitiate, user_id: str = Depends(current_user), db: Session = Depends(get_db)) -> UploadInitiated:
        require_membership(payload.organization_id, user_id, db)
        asset_id = str(uuid.uuid4())
        upload_key = f"organizations/{payload.organization_id}/assets/{asset_id}/{payload.original_filename}"
        asset = Asset(id=asset_id, organization_id=payload.organization_id, original_filename=payload.original_filename, media_type=payload.media_type, upload_key=upload_key)
        job = ProcessingJob(asset_id=asset_id)
        db.add_all([asset, job])
        db.commit()
        return UploadInitiated(asset_id=asset_id, upload_id=job.id, upload_key=upload_key, status=asset.status)

    @app.get("/api/v1/assets", response_model=list[AssetOut])
    def list_assets(organization_id: str, user_id: str = Depends(current_user), db: Session = Depends(get_db)) -> list[Asset]:
        require_membership(organization_id, user_id, db)
        return list(db.scalars(select(Asset).where(Asset.organization_id == organization_id).order_by(Asset.created_at.desc())))

    @app.get("/api/v1/assets/{asset_id}", response_model=AssetOut)
    def get_asset(asset_id: str, user_id: str = Depends(current_user), db: Session = Depends(get_db)) -> Asset:
        asset = get_asset_or_404(asset_id, db)
        require_membership(asset.organization_id, user_id, db)
        return asset

    @app.get("/api/v1/assets/{asset_id}/processing-job", response_model=ProcessingJobOut)
    def get_processing_job(asset_id: str, user_id: str = Depends(current_user), db: Session = Depends(get_db)) -> ProcessingJob:
        asset = get_asset_or_404(asset_id, db)
        require_membership(asset.organization_id, user_id, db)
        job = db.scalar(select(ProcessingJob).where(ProcessingJob.asset_id == asset_id).order_by(ProcessingJob.created_at.desc()))
        if not job:
            raise HTTPException(status_code=404, detail="Processing job not found")
        return job

    @app.patch("/api/v1/internal/assets/{asset_id}/processing", response_model=ProcessingJobOut, dependencies=[Depends(require_internal_token)])
    def update_processing(asset_id: str, payload: ProcessingUpdate, db: Session = Depends(get_db)) -> ProcessingJob:
        asset = get_asset_or_404(asset_id, db)
        job = db.scalar(select(ProcessingJob).where(ProcessingJob.asset_id == asset_id).order_by(ProcessingJob.created_at.desc()))
        if not job:
            raise HTTPException(status_code=404, detail="Processing job not found")
        asset.status = payload.status
        asset.byte_size = payload.byte_size if payload.byte_size is not None else asset.byte_size
        asset.duration_ms = payload.duration_ms if payload.duration_ms is not None else asset.duration_ms
        asset.width = payload.width if payload.width is not None else asset.width
        asset.height = payload.height if payload.height is not None else asset.height
        asset.error_message = payload.error_message
        job.stage, job.status, job.progress, job.error_message = payload.stage, payload.status, payload.progress, payload.error_message
        db.commit()
        db.refresh(job)
        return job

    return app


def seed_demo_data(db: Session) -> None:
    if db.get(Organization, "demo-org"):
        return
    db.add(Organization(id="demo-org", name="Mediaflow Demo", slug="mediaflow-demo"))
    db.add(OrganizationMember(organization_id="demo-org", user_id="demo-user", role=Role.OWNER))
    db.commit()


def get_asset_or_404(asset_id: str, db: Session) -> Asset:
    asset = db.get(Asset, asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    return asset


def error_code(status_code: int) -> str:
    return {401: "authentication_error", 403: "authorization_error", 404: "not_found", 409: "conflict"}.get(status_code, "request_error")


def error_response(request: Request, status_code: int, detail: str, code: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"detail": detail, "code": code, "request_id": request.state.request_id})


app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=int(os.getenv("PORT", "8000")), reload=True)
