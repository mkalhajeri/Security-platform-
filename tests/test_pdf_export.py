import base64
import io

from httpx import ASGITransport, AsyncClient
from PIL import Image as PILImage

from app.main import app
from app.routers.incidents import _json_safe
from tests.conftest import create_incident


def _png_bytes(color=(80, 120, 200), size=(60, 40)) -> bytes:
    buf = io.BytesIO()
    PILImage.new("RGB", size, color=color).save(buf, format="PNG")
    return buf.getvalue()


async def test_pdf_export_returns_valid_pdf(client):
    created = (await create_incident(client)).json()
    resp = await client.get(f"/incidents/{created['id']}/pdf")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content[:5] == b"%PDF-"
    assert len(resp.content) > 500


async def test_pdf_export_requires_authentication(test_db):
    # test_db (unused directly) puts a mock database behind get_database —
    # in a real deployment the DB is always connected before any request
    # is served; only the test harness needs this made explicit.
    anon = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    resp = await anon.get("/incidents/000000000000000000000000/pdf")
    assert resp.status_code == 401


async def test_pdf_export_404_for_missing_incident(client):
    resp = await client.get("/incidents/000000000000000000000000/pdf")
    assert resp.status_code == 404


async def test_pdf_export_embeds_real_picture(client):
    created = (await create_incident(client)).json()
    files = {"file": ("scene.png", _png_bytes(), "image/png")}
    await client.post(
        f"/incidents/{created['id']}/attachments",
        params={"kind": "picture"},
        files=files,
        data={"description": "Test scene"},
    )
    resp = await client.get(f"/incidents/{created['id']}/pdf")
    assert resp.status_code == 200
    assert resp.content[:5] == b"%PDF-"


async def test_pdf_export_skips_corrupted_picture_without_crashing(client):
    created = (await create_incident(client)).json()
    files = {"file": ("bad.png", b"not-a-real-image-at-all", "image/png")}
    await client.post(f"/incidents/{created['id']}/attachments", params={"kind": "picture"}, files=files)

    resp = await client.get(f"/incidents/{created['id']}/pdf")
    assert resp.status_code == 200
    assert resp.content[:5] == b"%PDF-"


async def test_pdf_export_embeds_drawn_signature(client):
    created = (await create_incident(client)).json()
    signature_data_uri = "data:image/png;base64," + base64.b64encode(_png_bytes((20, 20, 20), (300, 100))).decode()
    resp = await client.patch(
        f"/incidents/{created['id']}",
        json={"reviewed_by": {"signed_date": "2026-09-08", "signature_image": signature_data_uri}},
    )
    assert resp.status_code == 200

    resp = await client.get(f"/incidents/{created['id']}/pdf")
    assert resp.status_code == 200
    assert resp.content[:5] == b"%PDF-"


def test_json_safe_unwraps_string_enums_to_plain_values():
    """Regression test: every enum here also subclasses str, so a naive
    `isinstance(value, str)` guard never unwraps the member — it stays a
    real Enum instance whose str() prints "ClassName.MEMBER" instead of the
    value. Invisible once real MongoDB round-trips the field back as a
    plain string, but a real bug for anything (like PDF export) that
    renders a freshly-built dict via str() before that round-trip."""
    from app.models import Gender

    assert _json_safe(Gender.M) == "M"
    assert isinstance(_json_safe(Gender.M), str)
    assert not isinstance(_json_safe(Gender.M), Gender)
    assert _json_safe({"gender": Gender.M}) == {"gender": "M"}
    assert _json_safe([Gender.M, Gender.F]) == ["M", "F"]
