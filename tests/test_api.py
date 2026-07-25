from fastapi.testclient import TestClient

from app.main import create_app


def client(tmp_path):
    app = create_app(database_url=f"sqlite:///{tmp_path / 'test.db'}", internal_worker_token="worker-secret", media_base_url="http://media.test")
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


def test_frontend_upload_lifecycle(tmp_path):
    with client(tmp_path) as api:
        session = api.post("/api/v1/auth/login", json={"email": "alex@northstar.studio", "password": "mediaflow-demo"})
        headers = {"Authorization": f"Bearer {session.json()['access_token']}"}

        initiated = api.post("/api/v1/uploads/initiate", headers=headers, json={"organization_id": "demo-org", "original_filename": "new-story.mp4", "media_type": "video"})
        assert initiated.status_code == 201
        assert initiated.json()["status"] == "uploading"

        completed = api.post(f"/api/v1/uploads/{initiated.json()['upload_id']}/complete", headers=headers, json={"byte_size": 1234})
        assert completed.status_code == 200
        assert completed.json()["status"] == "processing"

        processed = api.patch(f"/api/v1/internal/assets/{initiated.json()['asset_id']}/processing", headers={"X-Internal-Token": "worker-secret"}, json={"stage": "proxy", "status": "completed", "progress": 100})
        assert processed.status_code == 200
        asset = api.get(f"/api/v1/assets/{initiated.json()['asset_id']}", headers=headers)
        assert asset.json()["status"] == "ready"


def test_upload_abort_is_authorized_and_terminal(tmp_path):
    with client(tmp_path) as api:
        initiated = api.post("/api/v1/uploads/initiate", headers={"X-User-Id": "demo-user"}, json={"organization_id": "demo-org", "original_filename": "cancel-me.mp4", "media_type": "video"})
        aborted = api.post(f"/api/v1/uploads/{initiated.json()['upload_id']}/abort", headers={"X-User-Id": "demo-user"})
        assert aborted.status_code == 204
        asset = api.get(f"/api/v1/assets/{initiated.json()['asset_id']}", headers={"X-User-Id": "demo-user"})
        assert asset.json()["status"] == "failed"


def test_worker_derivatives_enable_playback_and_retry(tmp_path):
    with client(tmp_path) as api:
        worker = {"X-Internal-Token": "worker-secret"}
        update = api.patch("/api/v1/internal/assets/customer-story/processing", headers=worker, json={"stage": "proxy", "status": "completed", "progress": 100, "proxy_key": "proxies/customer story.mp4", "thumbnail_key": "thumbs/customer-story.jpg"})
        assert update.status_code == 200
        playback = api.get("/api/v1/assets/customer-story/playback-url", headers={"X-User-Id": "demo-user"})
        assert playback.status_code == 200
        assert playback.json()["url"] == "http://media.test/proxies/customer%20story.mp4"

        failed = api.post("/api/v1/uploads/initiate", headers={"X-User-Id": "demo-user"}, json={"organization_id": "demo-org", "original_filename": "retry.mp4", "media_type": "video"})
        api.post(f"/api/v1/uploads/{failed.json()['upload_id']}/abort", headers={"X-User-Id": "demo-user"})
        retry = api.post(f"/api/v1/assets/{failed.json()['asset_id']}/retry", headers={"X-User-Id": "demo-user"})
        assert retry.status_code == 200
        assert retry.json()["status"] == "queued"


def test_worker_can_idempotently_persist_transcript_and_moments_for_search(tmp_path):
    with client(tmp_path) as api:
        asset = api.post("/api/v1/uploads/initiate", headers={"X-User-Id": "demo-user"}, json={"organization_id": "demo-org", "original_filename": "retention.mp4", "media_type": "video"}).json()
        worker = {"X-Internal-Token": "worker-secret"}
        transcript_payload = {"segments": [{"start_ms": 1000, "end_ms": 5000, "speaker": "Customer", "text": "We retained more customers after the onboarding changes."}]}
        transcript = api.put(f"/api/v1/internal/assets/{asset['asset_id']}/transcript", headers=worker, json=transcript_payload)
        assert transcript.status_code == 200
        assert transcript.json() == {"count": 1}

        moments_payload = {"moments": [{"id": "retention-moment", "title": "Higher customer retention", "start_ms": 1000, "end_ms": 5000, "category": "Testimonial", "score": 95}]}
        first = api.put(f"/api/v1/internal/assets/{asset['asset_id']}/moments", headers=worker, json=moments_payload)
        second = api.put(f"/api/v1/internal/assets/{asset['asset_id']}/moments", headers=worker, json=moments_payload)
        assert first.json() == second.json() == {"count": 1}

        session = api.post("/api/v1/auth/login", json={"email": "alex@northstar.studio", "password": "mediaflow-demo"})
        results = api.post("/api/v1/search", headers={"Authorization": f"Bearer {session.json()['access_token']}"}, json={"query": "retained customers"})
        assert any(result["moment_id"] == "retention-moment" for result in results.json()["results"])


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


def test_search_and_collections_are_scoped_to_authenticated_organization(tmp_path):
    with client(tmp_path) as api:
        session = api.post("/api/v1/auth/login", json={"email": "alex@northstar.studio", "password": "mediaflow-demo"})
        headers = {"Authorization": f"Bearer {session.json()['access_token']}"}

        search = api.post("/api/v1/search", headers=headers, json={"query": "easy onboarding"})
        assert search.status_code == 200
        result = search.json()["results"][0]
        assert result["asset_id"] == "customer-story"
        assert result["match_reasons"]

        created = api.post("/api/v1/collections", headers=headers, json={"name": "Customer voice", "description": "Useful proof points"})
        assert created.status_code == 201
        collection_id = created.json()["id"]
        added = api.post(f"/api/v1/collections/{collection_id}/items", headers=headers, json={"moment_id": result["moment_id"]})
        assert added.status_code == 201
        assert added.json()["item_count"] == 1

        items = api.get("/api/v1/collections", headers=headers)
        assert items.status_code == 200
        assert items.json()[0]["name"] == "Customer voice"
