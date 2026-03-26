"""Tests for the API endpoints and WebSocket command processing."""
import pytest
from httpx import ASGITransport, AsyncClient
from server import app


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ── SVG Preprocessing API ──

class TestPreprocessSvgEndpoint:
    @pytest.mark.anyio
    async def test_simple_svg(self, client):
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">'
            '<path d="M 0 0 L 50 50 L 100 0"/>'
            '</svg>'
        )
        resp = await client.post(
            "/api/preprocess-svg",
            files={"file": ("test.svg", svg.encode(), "image/svg+xml")},
            data={"simplify": "0"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "paths" in data
        assert "bbox" in data
        assert "stats" in data
        assert data["stats"]["simplified_paths"] >= 1
        assert data["stats"]["total_points"] >= 2

    @pytest.mark.anyio
    async def test_svg_with_rect(self, client):
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">'
            '<rect x="10" y="10" width="80" height="80"/>'
            '</svg>'
        )
        resp = await client.post(
            "/api/preprocess-svg",
            files={"file": ("rect.svg", svg.encode(), "image/svg+xml")},
            data={"simplify": "0"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["stats"]["simplified_paths"] >= 1
        # Rectangle has 5 points (closed)
        assert data["stats"]["total_points"] >= 4

    @pytest.mark.anyio
    async def test_svg_with_transform(self, client):
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">'
            '<g transform="translate(10, 20)">'
            '<path d="M 0 0 L 50 0"/>'
            '</g>'
            '</svg>'
        )
        resp = await client.post(
            "/api/preprocess-svg",
            files={"file": ("t.svg", svg.encode(), "image/svg+xml")},
            data={"simplify": "0"},
        )
        data = resp.json()
        assert data["stats"]["simplified_paths"] == 1
        path = data["paths"][0]
        # First point should be translated
        assert path[0][0] == pytest.approx(10, abs=0.1)
        assert path[0][1] == pytest.approx(20, abs=0.1)

    @pytest.mark.anyio
    async def test_svg_with_multiple_subpaths(self, client):
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">'
            '<path d="M 0 0 L 10 10 M 20 20 L 30 30 M 40 40 L 50 50"/>'
            '</svg>'
        )
        resp = await client.post(
            "/api/preprocess-svg",
            files={"file": ("multi.svg", svg.encode(), "image/svg+xml")},
            data={"simplify": "0"},
        )
        data = resp.json()
        assert data["stats"]["simplified_paths"] == 3

    @pytest.mark.anyio
    async def test_simplification_reduces_points(self, client):
        # Many collinear points
        points = " ".join(f"L {i} 0" for i in range(1, 100))
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">'
            f'<path d="M 0 0 {points}"/>'
            '</svg>'
        )
        resp = await client.post(
            "/api/preprocess-svg",
            files={"file": ("s.svg", svg.encode(), "image/svg+xml")},
            data={"simplify": "1"},
        )
        data = resp.json()
        # Collinear points should simplify to just 2
        assert data["stats"]["total_points"] == 2

    @pytest.mark.anyio
    async def test_invalid_svg(self, client):
        resp = await client.post(
            "/api/preprocess-svg",
            files={"file": ("bad.svg", b"not xml", "image/svg+xml")},
            data={"simplify": "0"},
        )
        assert resp.status_code == 400
        assert "error" in resp.json()

    @pytest.mark.anyio
    async def test_empty_svg(self, client):
        svg = '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"></svg>'
        resp = await client.post(
            "/api/preprocess-svg",
            files={"file": ("empty.svg", svg.encode(), "image/svg+xml")},
            data={"simplify": "0"},
        )
        data = resp.json()
        assert data["stats"]["simplified_paths"] == 0

    @pytest.mark.anyio
    async def test_bbox_correct(self, client):
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">'
            '<path d="M 10 20 L 30 40"/>'
            '</svg>'
        )
        resp = await client.post(
            "/api/preprocess-svg",
            files={"file": ("bb.svg", svg.encode(), "image/svg+xml")},
            data={"simplify": "0"},
        )
        data = resp.json()
        bbox = data["bbox"]
        assert bbox["minX"] == pytest.approx(10, abs=0.1)
        assert bbox["minY"] == pytest.approx(20, abs=0.1)
        assert bbox["maxX"] == pytest.approx(30, abs=0.1)
        assert bbox["maxY"] == pytest.approx(40, abs=0.1)

    @pytest.mark.anyio
    async def test_auto_simplify(self, client):
        """When simplify=0, auto-tolerance is used (0.1% of diagonal)."""
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="1000">'
            '<path d="M 0 0 L 500 0.01 L 1000 0"/>'
            '</svg>'
        )
        resp = await client.post(
            "/api/preprocess-svg",
            files={"file": ("auto.svg", svg.encode(), "image/svg+xml")},
            data={"simplify": "0"},
        )
        data = resp.json()
        # 0.01 deviation on 1414 diagonal = auto tolerance ~1.4, should simplify
        assert data["stats"]["total_points"] == 2


# ── Static file serving ──

class TestStaticServing:
    @pytest.mark.anyio
    async def test_index_page(self, client):
        resp = await client.get("/")
        assert resp.status_code == 200
        assert "Plotter Console" in resp.text

    @pytest.mark.anyio
    async def test_ports_api(self, client):
        resp = await client.get("/api/ports")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)
