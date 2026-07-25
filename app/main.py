from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import mimetypes
import os
import uuid
from pathlib import Path
from urllib.parse import quote
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from enum import StrEnum
from typing import Generator

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import DateTime, ForeignKey, Integer, String, create_engine, delete, func, inspect, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="MEDIAFLOW_",
        extra="ignore",
    )

    database_url: str = "sqlite:///./mediaflow-demo.db"
    internal_worker_token: str = "change-me-before-sharing"
    jwt_secret: str = "replace-with-a-long-random-local-secret"
    media_base_url: str = "http://127.0.0.1:3000/api/v1/media"
    media_storage_path: str = "./var/media"
    max_upload_bytes: int = 10 * 1024 * 1024 * 1024


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


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


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
    checksum_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider_asset_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
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


class AssetDerivative(Base):
    __tablename__ = "asset_derivatives"

    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"), primary_key=True)
    proxy_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    thumbnail_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class TranscriptSegment(Base):
    __tablename__ = "transcript_segments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"), index=True)
    start_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    end_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    speaker: Mapped[str | None] = mapped_column(String(120), nullable=True)
    text: Mapped[str] = mapped_column(String(2000), nullable=False)


class MediaMoment(Base):
    __tablename__ = "media_moments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"), index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    start_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    end_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False)


class Collection(Base):
    __tablename__ = "collections"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class CollectionItem(Base):
    __tablename__ = "collection_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    collection_id: Mapped[str] = mapped_column(ForeignKey("collections.id"), index=True)
    moment_id: Mapped[str] = mapped_column(ForeignKey("media_moments.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


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
    original_filename: str = Field(min_length=1, max_length=255, pattern=r"^[^/\\]+$")
    media_type: str = Field(pattern=r"^(video|image|audio)$")


class UploadInitiated(BaseModel):
    asset_id: str
    upload_id: str
    upload_key: str
    upload_url: str
    upload_method: str = "PUT"
    status: str


class UploadContentStored(BaseModel):
    asset_id: str
    upload_id: str
    byte_size: int


class UploadComplete(BaseModel):
    byte_size: int | None = Field(default=None, ge=0)
    checksum_sha256: str | None = Field(default=None, pattern=r"^[a-fA-F0-9]{64}$")


class UploadCompleted(BaseModel):
    asset_id: str
    upload_id: str
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
    checksum_sha256: str | None
    provider_asset_id: str | None
    error_message: str | None
    preview_url: str | None = None
    thumbnail_url: str | None = None
    playback_url: str | None = None


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
    checksum_sha256: str | None = Field(default=None, pattern=r"^[a-fA-F0-9]{64}$")
    provider_asset_id: str | None = Field(default=None, max_length=255)
    proxy_key: str | None = Field(default=None, max_length=500)
    thumbnail_key: str | None = Field(default=None, max_length=500)
    error_message: str | None = Field(default=None, max_length=500)


class PlaybackUrlOut(BaseModel):
    url: str
    expires_in: int = 300


class IngestionClaimRequest(BaseModel):
    worker_id: str = Field(default="local-worker", min_length=1, max_length=120)
    limit: int = Field(default=1, ge=1, le=10)


class IngestionWorkItem(BaseModel):
    asset_id: str
    job_id: str
    organization_id: str
    upload_key: str
    original_filename: str
    media_type: str
    byte_size: int | None


class IngestionClaimResponse(BaseModel):
    jobs: list[IngestionWorkItem]


class WorkerTranscriptSegment(BaseModel):
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    speaker: str | None = Field(default=None, max_length=120)
    text: str = Field(min_length=1, max_length=2000)


class WorkerTranscriptReplace(BaseModel):
    segments: list[WorkerTranscriptSegment] = Field(max_length=2_000)


class WorkerMoment(BaseModel):
    id: str = Field(min_length=1, max_length=36)
    title: str = Field(min_length=1, max_length=255)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    category: str = Field(min_length=1, max_length=100)
    score: int = Field(ge=0, le=100)


class WorkerMomentsReplace(BaseModel):
    moments: list[WorkerMoment] = Field(max_length=500)


class PersistedCount(BaseModel):
    count: int


class LoginInput(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=255)


class RegisterInput(LoginInput):
    name: str = Field(min_length=1, max_length=120)


class SessionOut(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_in: int = 900


class TranscriptOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    start_ms: int
    end_ms: int
    speaker: str | None
    text: str


class MomentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    title: str
    start_ms: int
    end_ms: int
    category: str
    score: int


class SearchInput(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    organization_id: str | None = None


class SearchResultOut(BaseModel):
    asset_id: str
    asset_name: str
    media_type: str
    moment_id: str
    title: str
    start_ms: int
    end_ms: int
    excerpt: str
    match_reasons: list[str]
    score: float
    preview_url: str | None = None
    thumbnail_url: str | None = None
    playback_url: str | None = None


class SearchResponse(BaseModel):
    search_id: str
    results: list[SearchResultOut]


class CollectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    organization_id: str | None = None


class CollectionItemCreate(BaseModel):
    moment_id: str


class CollectionOut(BaseModel):
    id: str
    organization_id: str
    name: str
    description: str | None
    item_count: int


def make_engine(database_url: str) -> Engine:
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    return create_engine(database_url, connect_args=connect_args)


def create_app(
    database_url: str | None = None,
    internal_worker_token: str | None = None,
    jwt_secret: str | None = None,
    media_base_url: str | None = None,
    media_storage_path: str | None = None,
) -> FastAPI:
    settings = Settings()
    engine = make_engine(database_url or settings.database_url)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    token = internal_worker_token or settings.internal_worker_token
    signing_secret = jwt_secret or settings.jwt_secret
    configured_media_base_url = media_base_url or settings.media_base_url
    storage_root = Path(media_storage_path or settings.media_storage_path).expanduser().resolve()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        storage_root.mkdir(parents=True, exist_ok=True)
        Base.metadata.create_all(engine)
        ensure_demo_schema(engine)
        with session_factory() as db:
            seed_demo_data(db)
        yield

    app = FastAPI(title="Mediaflow Demo API", version="0.1.0", lifespan=lifespan)
    app.state.session_factory = session_factory
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def request_id(request: Request, call_next):
        request.state.request_id = request.headers.get("X-Request-Id", str(uuid.uuid4()))
        response = await call_next(request)
        response.headers["X-Request-Id"] = request.state.request_id
        return response

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        return error_response(request, exc.status_code, str(exc.detail), error_code(exc.status_code, str(exc.detail)))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, _: RequestValidationError):
        return error_response(request, 422, "Request validation failed", "validation_error")

    def get_db() -> Generator[Session, None, None]:
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    def current_user(
        authorization: str | None = Header(default=None),
        x_user_id: str | None = Header(default=None),
    ) -> str:
        if authorization:
            scheme, _, value = authorization.partition(" ")
            if scheme.lower() != "bearer" or not value:
                raise HTTPException(status_code=401, detail="Malformed bearer token")
            return decode_access_token(value, signing_secret)
        # Maintained only for Team C's existing local worker/demo contract.
        if x_user_id:
            return x_user_id
        raise HTTPException(status_code=401, detail="Bearer token is required")

    def require_internal_token(x_internal_token: str | None = Header(default=None)) -> None:
        if not x_internal_token or x_internal_token != token:
            raise HTTPException(status_code=401, detail="Invalid internal worker token")

    def require_membership(organization_id: str, user_id: str, db: Session) -> OrganizationMember:
        membership = db.get(OrganizationMember, {"organization_id": organization_id, "user_id": user_id})
        if not membership:
            raise HTTPException(status_code=403, detail="You do not have access to this organization")
        return membership

    def storage_path(key: str) -> Path:
        if not key or key.startswith("/") or ".." in key.split("/"):
            raise HTTPException(status_code=422, detail="Storage key must be a safe relative path")
        target = (storage_root / key).resolve()
        if storage_root != target and storage_root not in target.parents:
            raise HTTPException(status_code=422, detail="Storage key escapes the media directory")
        return target

    def media_url(key: str) -> str:
        return f"{configured_media_base_url.rstrip('/')}/{quote(key, safe='/')}"

    def asset_output(asset: Asset, db: Session) -> AssetOut:
        derivative = db.get(AssetDerivative, asset.id)
        proxy_key = derivative.proxy_key if derivative else None
        thumbnail_key = derivative.thumbnail_key if derivative else None
        original_exists = storage_path(asset.upload_key).is_file()
        proxy_exists = bool(proxy_key and storage_path(proxy_key).is_file())
        thumbnail_exists = bool(thumbnail_key and storage_path(thumbnail_key).is_file())
        playable_key = proxy_key if proxy_exists else asset.upload_key if original_exists else None
        preview_key = (
            thumbnail_key
            if thumbnail_exists
            else playable_key
        )
        return AssetOut.model_validate(asset).model_copy(
            update={
                "preview_url": media_url(preview_key) if preview_key else None,
                "thumbnail_url": media_url(thumbnail_key) if thumbnail_exists else None,
                "playback_url": media_url(playable_key) if playable_key else None,
            }
        )

    def organization_for_request(organization_id: str | None, user_id: str, db: Session) -> str:
        if organization_id:
            require_membership(organization_id, user_id, db)
            return organization_id
        membership = db.scalar(
            select(OrganizationMember)
            .where(OrganizationMember.user_id == user_id)
            .order_by(OrganizationMember.created_at)
        )
        if not membership:
            raise HTTPException(status_code=403, detail="You do not belong to an organization")
        return membership.organization_id

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/v1/media/{key:path}")
    @app.head("/api/v1/media/{key:path}")
    def get_local_media(key: str) -> FileResponse:
        target = storage_path(key)
        if not target.is_file():
            raise HTTPException(status_code=404, detail="Media not found")
        return FileResponse(
            target,
            media_type=mimetypes.guess_type(target.name)[0] or "application/octet-stream",
            filename=None,
        )

    @app.post("/api/v1/auth/register", response_model=SessionOut, status_code=status.HTTP_201_CREATED)
    def register(payload: RegisterInput, db: Session = Depends(get_db)) -> SessionOut:
        if db.scalar(select(User).where(User.email == payload.email.lower())):
            raise HTTPException(status_code=409, detail="An account already exists for this email")
        user = User(email=payload.email.lower(), name=payload.name, password_hash=password_hash(payload.password))
        db.add(user)
        db.commit()
        db.refresh(user)
        return SessionOut(access_token=encode_access_token(user.id, signing_secret))

    @app.post("/api/v1/auth/login", response_model=SessionOut)
    def login(payload: LoginInput, db: Session = Depends(get_db)) -> SessionOut:
        user = db.scalar(select(User).where(User.email == payload.email.lower()))
        if not user or not password_matches(payload.password, user.password_hash):
            raise HTTPException(status_code=401, detail="Invalid email or password")
        return SessionOut(access_token=encode_access_token(user.id, signing_secret))

    @app.get("/api/v1/auth/me")
    def me(user_id: str = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, str]:
        user = db.get(User, user_id)
        if not user:
            raise HTTPException(status_code=401, detail="Token subject no longer exists")
        return {"id": user.id, "email": user.email, "name": user.name}

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
        asset = Asset(id=asset_id, organization_id=payload.organization_id, original_filename=payload.original_filename, media_type=payload.media_type, upload_key=upload_key, status="uploading")
        job = ProcessingJob(asset_id=asset_id, stage="upload", status="uploading")
        db.add_all([asset, job])
        db.commit()
        return UploadInitiated(
            asset_id=asset_id,
            upload_id=job.id,
            upload_key=upload_key,
            upload_url=f"/api/v1/uploads/{job.id}/content",
            status=asset.status,
        )

    @app.put("/api/v1/uploads/{upload_id}/content", response_model=UploadContentStored)
    async def store_upload_content(
        upload_id: str,
        request: Request,
        user_id: str = Depends(current_user),
        db: Session = Depends(get_db),
    ) -> UploadContentStored:
        job = db.get(ProcessingJob, upload_id)
        if not job:
            raise HTTPException(status_code=404, detail="Upload not found")
        asset = get_asset_or_404(job.asset_id, db)
        require_membership(asset.organization_id, user_id, db)
        if asset.status != "uploading":
            raise HTTPException(status_code=409, detail="Upload is not accepting content")

        content_length = request.headers.get("content-length")
        if content_length:
            try:
                declared_size = int(content_length)
            except ValueError as exc:
                raise HTTPException(
                    status_code=400,
                    detail="Content-Length must be an integer",
                ) from exc
            if declared_size > settings.max_upload_bytes:
                raise HTTPException(status_code=413, detail="Upload exceeds the local size limit")

        target = storage_path(asset.upload_key)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.parent / f".{target.name}.{uuid.uuid4().hex}.part"
        byte_size = 0
        try:
            with temporary.open("wb") as handle:
                async for chunk in request.stream():
                    if not chunk:
                        continue
                    byte_size += len(chunk)
                    if byte_size > settings.max_upload_bytes:
                        raise HTTPException(
                            status_code=413,
                            detail="Upload exceeds the local size limit",
                        )
                    handle.write(chunk)
            if byte_size == 0:
                raise HTTPException(status_code=400, detail="Upload content is empty")
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

        asset.byte_size = byte_size
        db.commit()
        return UploadContentStored(asset_id=asset.id, upload_id=job.id, byte_size=byte_size)

    @app.post("/api/v1/uploads/{upload_id}/complete", response_model=UploadCompleted)
    def complete_upload(upload_id: str, payload: UploadComplete, user_id: str = Depends(current_user), db: Session = Depends(get_db)) -> UploadCompleted:
        job = db.get(ProcessingJob, upload_id)
        if not job:
            raise HTTPException(status_code=404, detail="Upload not found")
        asset = get_asset_or_404(job.asset_id, db)
        require_membership(asset.organization_id, user_id, db)
        if asset.status != "uploading":
            raise HTTPException(status_code=409, detail="Upload is not awaiting completion")
        target = storage_path(asset.upload_key)
        if not target.is_file():
            raise HTTPException(status_code=409, detail="Upload content has not been stored")
        actual_size = target.stat().st_size
        if payload.byte_size is not None and payload.byte_size != actual_size:
            raise HTTPException(status_code=409, detail="Uploaded byte size does not match")
        actual_checksum = file_checksum(target)
        if payload.checksum_sha256 and payload.checksum_sha256.lower() != actual_checksum:
            raise HTTPException(status_code=409, detail="Uploaded checksum does not match")
        assign_checksum(asset, actual_checksum, db)
        asset.status = "processing"
        asset.byte_size = actual_size
        job.stage = "queued"
        job.status = "queued"
        job.progress = 0
        db.commit()
        return UploadCompleted(asset_id=asset.id, upload_id=job.id, status=asset.status)

    @app.post("/api/v1/uploads/{upload_id}/abort", status_code=status.HTTP_204_NO_CONTENT)
    def abort_upload(upload_id: str, user_id: str = Depends(current_user), db: Session = Depends(get_db)) -> None:
        job = db.get(ProcessingJob, upload_id)
        if not job:
            raise HTTPException(status_code=404, detail="Upload not found")
        asset = get_asset_or_404(job.asset_id, db)
        require_membership(asset.organization_id, user_id, db)
        if asset.status != "uploading":
            raise HTTPException(status_code=409, detail="Upload can no longer be aborted")
        storage_path(asset.upload_key).unlink(missing_ok=True)
        asset.status = "failed"
        asset.error_message = "Upload aborted"
        job.stage = "upload"
        job.status = "failed"
        job.error_message = asset.error_message
        db.commit()

    @app.post("/api/v1/internal/workflows/ingest", response_model=IngestionClaimResponse, dependencies=[Depends(require_internal_token)])
    @app.post("/api/v1/internal/workflows/ingest/claim", response_model=IngestionClaimResponse, dependencies=[Depends(require_internal_token)])
    def claim_ingestion_work(payload: IngestionClaimRequest, db: Session = Depends(get_db)) -> IngestionClaimResponse:
        queued = list(
            db.execute(
                select(ProcessingJob, Asset)
                .join(Asset, ProcessingJob.asset_id == Asset.id)
                .where(ProcessingJob.status == "queued", Asset.status == "processing")
                .order_by(ProcessingJob.created_at)
                .limit(payload.limit)
            )
        )
        jobs: list[IngestionWorkItem] = []
        for job, asset in queued:
            job.stage = "preparing_file"
            job.status = "processing"
            job.progress = 15
            jobs.append(
                IngestionWorkItem(
                    asset_id=asset.id,
                    job_id=job.id,
                    organization_id=asset.organization_id,
                    upload_key=asset.upload_key,
                    original_filename=asset.original_filename,
                    media_type=asset.media_type,
                    byte_size=asset.byte_size,
                )
            )
        db.commit()
        return IngestionClaimResponse(jobs=jobs)

    @app.get("/api/v1/assets", response_model=list[AssetOut])
    def list_assets(organization_id: str, user_id: str = Depends(current_user), db: Session = Depends(get_db)) -> list[AssetOut]:
        require_membership(organization_id, user_id, db)
        assets = list(db.scalars(select(Asset).where(Asset.organization_id == organization_id).order_by(Asset.created_at.desc())))
        return [asset_output(asset, db) for asset in assets]

    @app.get("/api/v1/assets/{asset_id}", response_model=AssetOut)
    def get_asset(asset_id: str, user_id: str = Depends(current_user), db: Session = Depends(get_db)) -> AssetOut:
        asset = get_asset_or_404(asset_id, db)
        require_membership(asset.organization_id, user_id, db)
        return asset_output(asset, db)

    @app.get("/api/v1/assets/{asset_id}/transcript", response_model=list[TranscriptOut])
    def get_transcript(asset_id: str, user_id: str = Depends(current_user), db: Session = Depends(get_db)) -> list[TranscriptSegment]:
        asset = get_asset_or_404(asset_id, db)
        require_membership(asset.organization_id, user_id, db)
        return list(db.scalars(select(TranscriptSegment).where(TranscriptSegment.asset_id == asset_id).order_by(TranscriptSegment.start_ms)))

    @app.get("/api/v1/assets/{asset_id}/moments", response_model=list[MomentOut])
    def get_moments(asset_id: str, user_id: str = Depends(current_user), db: Session = Depends(get_db)) -> list[MediaMoment]:
        asset = get_asset_or_404(asset_id, db)
        require_membership(asset.organization_id, user_id, db)
        return list(db.scalars(select(MediaMoment).where(MediaMoment.asset_id == asset_id).order_by(MediaMoment.start_ms)))

    @app.get("/api/v1/assets/{asset_id}/playback-url", response_model=PlaybackUrlOut)
    def get_playback_url(asset_id: str, user_id: str = Depends(current_user), db: Session = Depends(get_db)) -> PlaybackUrlOut:
        asset = get_asset_or_404(asset_id, db)
        require_membership(asset.organization_id, user_id, db)
        derivative = db.get(AssetDerivative, asset_id)
        proxy_key = derivative.proxy_key if derivative else None
        if proxy_key and storage_path(proxy_key).is_file():
            playable_key = proxy_key
        elif storage_path(asset.upload_key).is_file():
            playable_key = asset.upload_key
        else:
            raise HTTPException(status_code=409, detail="Playable media is not available yet")
        return PlaybackUrlOut(url=media_url(playable_key))

    @app.post("/api/v1/assets/{asset_id}/retry", response_model=ProcessingJobOut)
    def retry_asset(asset_id: str, user_id: str = Depends(current_user), db: Session = Depends(get_db)) -> ProcessingJob:
        asset = get_asset_or_404(asset_id, db)
        require_membership(asset.organization_id, user_id, db)
        if asset.status != "failed":
            raise HTTPException(status_code=409, detail="Only failed assets can be retried")
        job = db.scalar(select(ProcessingJob).where(ProcessingJob.asset_id == asset_id).order_by(ProcessingJob.created_at.desc()))
        if not job:
            raise HTTPException(status_code=404, detail="Processing job not found")
        asset.status = "processing"
        asset.error_message = None
        job.stage = "queued"
        job.status = "queued"
        job.progress = 0
        job.error_message = None
        db.commit()
        db.refresh(job)
        return job

    @app.post("/api/v1/search", response_model=SearchResponse)
    def search(payload: SearchInput, user_id: str = Depends(current_user), db: Session = Depends(get_db)) -> SearchResponse:
        organization_id = organization_for_request(payload.organization_id, user_id, db)
        terms = {term for term in payload.query.lower().split() if len(term) > 2}
        candidates = list(
            db.execute(
                select(MediaMoment, Asset)
                .join(Asset, MediaMoment.asset_id == Asset.id)
                .where(Asset.organization_id == organization_id)
            )
        )
        results: list[SearchResultOut] = []
        media_by_asset: dict[str, AssetOut] = {}
        for moment, asset in candidates:
            segment = db.scalar(
                select(TranscriptSegment)
                .where(TranscriptSegment.asset_id == asset.id, TranscriptSegment.start_ms <= moment.end_ms, TranscriptSegment.end_ms >= moment.start_ms)
                .order_by(TranscriptSegment.start_ms)
            )
            excerpt = segment.text if segment else moment.title
            searchable = f"{moment.title} {moment.category} {excerpt}".lower()
            matched_terms = sorted(term for term in terms if term in searchable)
            if terms and not matched_terms:
                continue
            score = round(len(matched_terms) / max(len(terms), 1), 2)
            media = media_by_asset.get(asset.id)
            if media is None:
                media = asset_output(asset, db)
                media_by_asset[asset.id] = media
            results.append(
                SearchResultOut(
                    asset_id=asset.id,
                    asset_name=asset.original_filename,
                    media_type=asset.media_type,
                    moment_id=moment.id,
                    title=moment.title,
                    start_ms=moment.start_ms,
                    end_ms=moment.end_ms,
                    excerpt=excerpt,
                    match_reasons=[f"Matched: {', '.join(matched_terms)}"] if matched_terms else ["Browse result"],
                    score=score,
                    preview_url=media.preview_url,
                    thumbnail_url=media.thumbnail_url,
                    playback_url=media.playback_url,
                )
            )
        results.sort(key=lambda item: item.score, reverse=True)
        return SearchResponse(search_id=str(uuid.uuid4()), results=results)

    @app.get("/api/v1/collections", response_model=list[CollectionOut])
    def list_collections(organization_id: str | None = None, user_id: str = Depends(current_user), db: Session = Depends(get_db)) -> list[CollectionOut]:
        org_id = organization_for_request(organization_id, user_id, db)
        collections = list(db.scalars(select(Collection).where(Collection.organization_id == org_id).order_by(Collection.updated_at.desc())))
        return [collection_output(collection, db) for collection in collections]

    @app.post("/api/v1/collections", response_model=CollectionOut, status_code=status.HTTP_201_CREATED)
    def create_collection(payload: CollectionCreate, user_id: str = Depends(current_user), db: Session = Depends(get_db)) -> CollectionOut:
        org_id = organization_for_request(payload.organization_id, user_id, db)
        collection = Collection(organization_id=org_id, name=payload.name, description=payload.description)
        db.add(collection)
        db.commit()
        db.refresh(collection)
        return collection_output(collection, db)

    @app.post("/api/v1/collections/{collection_id}/items", response_model=CollectionOut, status_code=status.HTTP_201_CREATED)
    def add_collection_item(collection_id: str, payload: CollectionItemCreate, user_id: str = Depends(current_user), db: Session = Depends(get_db)) -> CollectionOut:
        collection = get_collection_or_404(collection_id, db)
        require_membership(collection.organization_id, user_id, db)
        moment = db.get(MediaMoment, payload.moment_id)
        if not moment:
            raise HTTPException(status_code=404, detail="Moment not found")
        asset = get_asset_or_404(moment.asset_id, db)
        if asset.organization_id != collection.organization_id:
            raise HTTPException(status_code=403, detail="Moment belongs to another organization")
        if db.scalar(select(CollectionItem).where(CollectionItem.collection_id == collection_id, CollectionItem.moment_id == moment.id)):
            raise HTTPException(status_code=409, detail="Moment is already in this collection")
        db.add(CollectionItem(collection_id=collection_id, moment_id=moment.id))
        db.commit()
        return collection_output(collection, db)

    @app.delete("/api/v1/collections/{collection_id}/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
    def remove_collection_item(collection_id: str, item_id: str, user_id: str = Depends(current_user), db: Session = Depends(get_db)) -> None:
        collection = get_collection_or_404(collection_id, db)
        require_membership(collection.organization_id, user_id, db)
        item = db.get(CollectionItem, item_id)
        if not item or item.collection_id != collection_id:
            raise HTTPException(status_code=404, detail="Collection item not found")
        db.delete(item)
        db.commit()

    @app.get("/api/v1/assets/{asset_id}/processing-job", response_model=ProcessingJobOut)
    def get_processing_job(asset_id: str, user_id: str = Depends(current_user), db: Session = Depends(get_db)) -> ProcessingJob:
        asset = get_asset_or_404(asset_id, db)
        require_membership(asset.organization_id, user_id, db)
        job = db.scalar(select(ProcessingJob).where(ProcessingJob.asset_id == asset_id).order_by(ProcessingJob.created_at.desc()))
        if not job:
            raise HTTPException(status_code=404, detail="Processing job not found")
        return job

    @app.put("/api/v1/internal/assets/{asset_id}/transcript", response_model=PersistedCount, dependencies=[Depends(require_internal_token)])
    def replace_transcript(asset_id: str, payload: WorkerTranscriptReplace, db: Session = Depends(get_db)) -> PersistedCount:
        get_asset_or_404(asset_id, db)
        validate_time_ranges(payload.segments)
        db.execute(delete(TranscriptSegment).where(TranscriptSegment.asset_id == asset_id))
        db.add_all(
            TranscriptSegment(
                asset_id=asset_id,
                start_ms=segment.start_ms,
                end_ms=segment.end_ms,
                speaker=segment.speaker,
                text=segment.text,
            )
            for segment in payload.segments
        )
        db.commit()
        return PersistedCount(count=len(payload.segments))

    @app.put("/api/v1/internal/assets/{asset_id}/moments", response_model=PersistedCount, dependencies=[Depends(require_internal_token)])
    def replace_moments(asset_id: str, payload: WorkerMomentsReplace, db: Session = Depends(get_db)) -> PersistedCount:
        get_asset_or_404(asset_id, db)
        validate_time_ranges(payload.moments)
        existing = {moment.id: moment for moment in db.scalars(select(MediaMoment).where(MediaMoment.asset_id == asset_id))}
        incoming_ids = {moment.id for moment in payload.moments}
        for moment_id, moment in existing.items():
            if moment_id not in incoming_ids:
                db.execute(delete(CollectionItem).where(CollectionItem.moment_id == moment_id))
                db.delete(moment)
        for item in payload.moments:
            moment = existing.get(item.id)
            if moment:
                moment.title = item.title
                moment.start_ms = item.start_ms
                moment.end_ms = item.end_ms
                moment.category = item.category
                moment.score = item.score
            else:
                if db.get(MediaMoment, item.id):
                    raise HTTPException(status_code=409, detail="Moment identifier belongs to another asset")
                db.add(MediaMoment(id=item.id, asset_id=asset_id, title=item.title, start_ms=item.start_ms, end_ms=item.end_ms, category=item.category, score=item.score))
        db.commit()
        return PersistedCount(count=len(payload.moments))

    @app.patch("/api/v1/internal/assets/{asset_id}/processing", response_model=ProcessingJobOut, dependencies=[Depends(require_internal_token)])
    def update_processing(asset_id: str, payload: ProcessingUpdate, db: Session = Depends(get_db)) -> ProcessingJob:
        asset = get_asset_or_404(asset_id, db)
        job = db.scalar(select(ProcessingJob).where(ProcessingJob.asset_id == asset_id).order_by(ProcessingJob.created_at.desc()))
        if not job:
            raise HTTPException(status_code=404, detail="Processing job not found")
        asset.status = "ready" if payload.status == "completed" else payload.status
        asset.byte_size = payload.byte_size if payload.byte_size is not None else asset.byte_size
        asset.duration_ms = payload.duration_ms if payload.duration_ms is not None else asset.duration_ms
        asset.width = payload.width if payload.width is not None else asset.width
        asset.height = payload.height if payload.height is not None else asset.height
        assign_checksum(asset, payload.checksum_sha256, db)
        asset.provider_asset_id = payload.provider_asset_id if payload.provider_asset_id is not None else asset.provider_asset_id
        asset.error_message = payload.error_message
        job.stage, job.status, job.progress, job.error_message = payload.stage, payload.status, payload.progress, payload.error_message
        if payload.proxy_key is not None or payload.thumbnail_key is not None:
            validate_storage_key(payload.proxy_key)
            validate_storage_key(payload.thumbnail_key)
            derivative = db.get(AssetDerivative, asset_id) or AssetDerivative(asset_id=asset_id)
            derivative.proxy_key = payload.proxy_key if payload.proxy_key is not None else derivative.proxy_key
            derivative.thumbnail_key = payload.thumbnail_key if payload.thumbnail_key is not None else derivative.thumbnail_key
            db.add(derivative)
        db.commit()
        db.refresh(job)
        return job

    return app


def ensure_demo_schema(engine: Engine) -> None:
    """Apply the two additive SQLite columns needed by the local demo.

    Production moves to Alembic/Supabase migrations; this keeps existing local
    demo databases usable while the MVP remains SQLite-backed.
    """
    if engine.dialect.name != "sqlite":
        return
    existing_columns = {column["name"] for column in inspect(engine).get_columns("assets")}
    with engine.begin() as connection:
        if "checksum_sha256" not in existing_columns:
            connection.execute(text("ALTER TABLE assets ADD COLUMN checksum_sha256 VARCHAR(64)"))
        if "provider_asset_id" not in existing_columns:
            connection.execute(text("ALTER TABLE assets ADD COLUMN provider_asset_id VARCHAR(255)"))
        connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_assets_org_checksum ON assets (organization_id, checksum_sha256)"))


def seed_demo_data(db: Session) -> None:
    if db.get(Organization, "demo-org"):
        return
    db.add(Organization(id="demo-org", name="Mediaflow Demo", slug="mediaflow-demo"))
    db.add(User(id="demo-user", email="alex@northstar.studio", name="Alex Morgan", password_hash=password_hash("mediaflow-demo")))
    db.add(OrganizationMember(organization_id="demo-org", user_id="demo-user", role=Role.OWNER))
    asset = Asset(
        id="customer-story",
        organization_id="demo-org",
        original_filename="acme_interview_final_v3.mp4",
        media_type="video",
        upload_key="demo/customer-story.mp4",
        status="ready",
        duration_ms=1_122_000,
    )
    db.add(asset)
    db.add(ProcessingJob(asset_id=asset.id, stage="complete", status="completed", progress=100))
    db.add_all([
        TranscriptSegment(asset_id=asset.id, start_ms=0, end_ms=14_000, speaker="Interviewer", text="Tell me what implementation looked like before you started."),
        TranscriptSegment(asset_id=asset.id, start_ms=31_000, end_ms=48_000, speaker="Maya", text="The surprise was how easy onboarding felt. We connected our library on a Tuesday."),
        TranscriptSegment(asset_id=asset.id, start_ms=48_000, end_ms=67_000, speaker="Maya", text="By Thursday, the team was finding customer moments we had completely forgotten."),
    ])
    db.add_all([
        MediaMoment(id="moment-1", asset_id=asset.id, title="Onboarding was easier than expected", start_ms=31_000, end_ms=53_000, category="Testimonial", score=96),
        MediaMoment(id="moment-2", asset_id=asset.id, title="From forgotten footage to useful stories", start_ms=48_000, end_ms=64_000, category="Outcome", score=92),
    ])
    db.commit()


def get_asset_or_404(asset_id: str, db: Session) -> Asset:
    asset = db.get(Asset, asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    return asset


def file_checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def assign_checksum(asset: Asset, checksum_sha256: str | None, db: Session) -> None:
    if checksum_sha256 is None:
        return
    normalized = checksum_sha256.lower()
    duplicate = db.scalar(
        select(Asset).where(
            Asset.organization_id == asset.organization_id,
            Asset.checksum_sha256 == normalized,
            Asset.id != asset.id,
        )
    )
    if duplicate:
        raise HTTPException(status_code=409, detail="An identical asset already exists in this organization")
    asset.checksum_sha256 = normalized


def get_collection_or_404(collection_id: str, db: Session) -> Collection:
    collection = db.get(Collection, collection_id)
    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")
    return collection


def validate_storage_key(key: str | None) -> None:
    if key is None:
        return
    if key.startswith("/") or ".." in key.split("/"):
        raise HTTPException(status_code=422, detail="Storage key must be a relative object key")


def validate_time_ranges(items: list[WorkerTranscriptSegment] | list[WorkerMoment]) -> None:
    for item in items:
        if item.end_ms <= item.start_ms:
            raise HTTPException(status_code=422, detail="End time must be after start time")


def collection_output(collection: Collection, db: Session) -> CollectionOut:
    item_count = db.scalar(select(func.count()).select_from(CollectionItem).where(CollectionItem.collection_id == collection.id))
    return CollectionOut(
        id=collection.id,
        organization_id=collection.organization_id,
        name=collection.name,
        description=collection.description,
        item_count=item_count or 0,
    )


def error_code(status_code: int, detail: str = "") -> str:
    if status_code == 409 and "identical asset" in detail:
        return "duplicate_asset"
    return {401: "authentication_error", 403: "authorization_error", 404: "not_found", 409: "conflict"}.get(status_code, "request_error")


def error_response(request: Request, status_code: int, detail: str, code: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"code": code, "message": detail, "details": {"request_id": request.state.request_id}})


def password_hash(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"{salt.hex()}${digest.hex()}"


def password_matches(password: str, encoded: str) -> bool:
    salt_hex, digest_hex = encoded.split("$", 1)
    candidate = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), n=2**14, r=8, p=1)
    return hmac.compare_digest(candidate.hex(), digest_hex)


def b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def encode_access_token(user_id: str, secret: str) -> str:
    header = b64url(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = b64url(json.dumps({"sub": user_id, "iat": int(utcnow().timestamp()), "exp": int(utcnow().timestamp()) + 900}, separators=(",", ":")).encode())
    signature = b64url(hmac.new(secret.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest())
    return f"{header}.{payload}.{signature}"


def decode_access_token(token: str, secret: str) -> str:
    try:
        header, payload, signature = token.split(".")
        expected = b64url(hmac.new(secret.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest())
        claims = json.loads(b64url_decode(payload))
        if not hmac.compare_digest(signature, expected) or claims["exp"] <= int(utcnow().timestamp()):
            raise ValueError
        return str(claims["sub"])
    except (KeyError, ValueError, json.JSONDecodeError):
        raise HTTPException(status_code=401, detail="Invalid or expired bearer token") from None


app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=int(os.getenv("PORT", "3000")), reload=True)
