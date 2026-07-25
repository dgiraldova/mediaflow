from fastapi.testclient import TestClient

from app.main import create_app


def client(tmp_path):
    app = create_app(database_url=f"sqlite:///{tmp_path / 'test.db'}", internal_worker_token="worker-secret")
    return TestClient(app)


def test_upload_to_worker_update_to_status(tmp_path):
    with client(tmp_path) as api:
        upload = api.post("/api/v1/uploads/initiate", headers={"X-User-Id": "demo-user"}, json={"organization_id": "demo-org", "original_filename": "customer-story.mp4", "media_type": "video"})
        assert upload.status_code == 201
        asset_id = upload.json()["asset_id"]

        update = api.patch(f"/api/v1/internal/assets/{asset_id}/processing", headers={"X-Internal-Token": "worker-secret"}, json={"stage": "ffprobe", "status": "completed", "progress": 100, "duration_ms": 42000, "width": 1920, "height": 1080})
        assert update.status_code == 200
        assert update.json()["progress"] == 100

        asset = api.get(f"/api/v1/assets/{asset_id}", headers={"X-User-Id": "demo-user"})
        assert asset.status_code == 200
        assert asset.json()["duration_ms"] == 42000


def test_org_isolation_and_internal_token(tmp_path):
    with client(tmp_path) as api:
        created = api.post("/api/v1/organizations", headers={"X-User-Id": "other-user"}, json={"name": "Other", "slug": "other"})
        assert created.status_code == 201
        other_org = created.json()["id"]

        denied = api.get("/api/v1/assets", headers={"X-User-Id": "demo-user"}, params={"organization_id": other_org})
        assert denied.status_code == 403
        assert denied.json()["code"] == "authorization_error"

        upload = api.post("/api/v1/uploads/initiate", headers={"X-User-Id": "demo-user"}, json={"organization_id": "demo-org", "original_filename": "clip.mp4", "media_type": "video"})
        denied_worker = api.patch(f"/api/v1/internal/assets/{upload.json()['asset_id']}/processing", json={"stage": "ffprobe", "status": "processing", "progress": 25})
        assert denied_worker.status_code == 401
        assert denied_worker.json()["code"] == "authentication_error"


def test_requires_demo_user_header(tmp_path):
    with client(tmp_path) as api:
        response = api.get("/api/v1/organizations")
        assert response.status_code == 401
        assert response.json()["code"] == "authentication_error"
