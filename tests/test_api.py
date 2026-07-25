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


def test_frontend_login_and_asset_detail_contract(tmp_path):
    with client(tmp_path) as api:
        session = api.post("/api/v1/auth/login", json={"email": "alex@northstar.studio", "password": "mediaflow-demo"})
        assert session.status_code == 200
        assert session.json()["token_type"] == "Bearer"
        headers = {"Authorization": f"Bearer {session.json()['access_token']}"}

        profile = api.get("/api/v1/auth/me", headers=headers)
        assert profile.json()["email"] == "alex@northstar.studio"

        asset = api.get("/api/v1/assets/customer-story", headers=headers)
        assert asset.status_code == 200
        transcript = api.get("/api/v1/assets/customer-story/transcript", headers=headers)
        moments = api.get("/api/v1/assets/customer-story/moments", headers=headers)
        assert transcript.status_code == 200
        assert moments.status_code == 200
        assert moments.json()[0]["start_ms"] == 31_000


def test_frontend_error_envelope_and_cors(tmp_path):
    with client(tmp_path) as api:
        response = api.get("/api/v1/organizations", headers={"Origin": "http://localhost:5173"})
        assert response.status_code == 401
        assert response.json()["message"] == "Bearer token is required"
        assert response.json()["details"]["request_id"]
        assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
