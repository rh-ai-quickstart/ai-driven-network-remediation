"""Unit tests for the ServiceNow mock server.

Run via:
    cd hub/infra/servicenow-mock && uv sync --group dev && uv run pytest

Also included in: make unit-tests
"""

import pytest
from fastapi.testclient import TestClient
from main import app

HEADERS = {"X-API-Key": "demo-api-key-2026"}


@pytest.fixture
def client():
    return TestClient(app)


class TestAuth:
    def test_missing_key_returns_401(self, client):
        resp = client.post(
            "/api/now/table/incident",
            json={"record": {"short_description": "test"}},
        )
        assert resp.status_code == 401

    def test_wrong_key_returns_401(self, client):
        resp = client.post(
            "/api/now/table/incident",
            json={"record": {"short_description": "test"}},
            headers={"X-API-Key": "wrong-key"},
        )
        assert resp.status_code == 401

    def test_valid_key_passes(self, client):
        resp = client.post(
            "/api/now/table/incident",
            json={"record": {"short_description": "test"}},
            headers=HEADERS,
        )
        assert resp.status_code == 201


class TestCreateIncident:
    def test_returns_record_with_number_and_sys_id(self, client):
        resp = client.post(
            "/api/now/table/incident",
            json={"record": {"short_description": "Pod crash", "priority": "2"}},
            headers=HEADERS,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "record" in data
        assert data["record"]["number"].startswith("INC")
        assert len(data["record"]["sys_id"]) == 32
        assert data["record"]["short_description"] == "Pod crash"
        assert data["record"]["priority"] == "2"
        assert data["record"]["state"] == "1"

    def test_increments_ticket_numbers(self, client):
        r1 = client.post(
            "/api/now/table/incident",
            json={"record": {"short_description": "first"}},
            headers=HEADERS,
        )
        r2 = client.post(
            "/api/now/table/incident",
            json={"record": {"short_description": "second"}},
            headers=HEADERS,
        )
        n1 = int(r1.json()["record"]["number"][3:])
        n2 = int(r2.json()["record"]["number"][3:])
        assert n2 > n1


class TestGetIncident:
    def test_get_existing(self, client):
        created = client.post(
            "/api/now/table/incident",
            json={"record": {"short_description": "to retrieve", "priority": "1"}},
            headers=HEADERS,
        ).json()
        number = created["record"]["number"]

        resp = client.get(f"/api/now/table/incident/{number}", headers=HEADERS)
        assert resp.status_code == 200
        assert resp.json()["record"]["short_description"] == "to retrieve"
        assert resp.json()["record"]["priority"] == "1"

    def test_get_nonexistent_returns_404(self, client):
        resp = client.get("/api/now/table/incident/INC9999999", headers=HEADERS)
        assert resp.status_code == 404


class TestUpdateIncident:
    def test_patch_state(self, client):
        created = client.post(
            "/api/now/table/incident",
            json={"record": {"short_description": "to update"}},
            headers=HEADERS,
        ).json()
        number = created["record"]["number"]

        resp = client.patch(
            f"/api/now/table/incident/{number}",
            json={"record": {"state": "2"}},
            headers=HEADERS,
        )
        assert resp.status_code == 200
        assert resp.json()["record"]["state"] == "2"

    def test_patch_appends_work_notes(self, client):
        created = client.post(
            "/api/now/table/incident",
            json={"record": {"short_description": "work notes test"}},
            headers=HEADERS,
        ).json()
        number = created["record"]["number"]

        client.patch(
            f"/api/now/table/incident/{number}",
            json={"record": {"work_notes": "note 1"}},
            headers=HEADERS,
        )
        client.patch(
            f"/api/now/table/incident/{number}",
            json={"record": {"work_notes": "note 2"}},
            headers=HEADERS,
        )

        resp = client.get(f"/api/now/table/incident/{number}", headers=HEADERS)
        notes = resp.json()["record"]["work_notes"]
        assert len(notes) == 2
        assert notes[0]["text"] == "note 1"
        assert notes[1]["text"] == "note 2"

    def test_patch_nonexistent_returns_404(self, client):
        resp = client.patch(
            "/api/now/table/incident/INC9999999",
            json={"record": {"state": "6"}},
            headers=HEADERS,
        )
        assert resp.status_code == 404


class TestListIncidents:
    def test_list_all(self, client):
        created = client.post(
            "/api/now/table/incident",
            json={"record": {"short_description": "list test", "priority": "3"}},
            headers=HEADERS,
        ).json()
        number = created["record"]["number"]

        resp = client.get("/api/now/table/incident", headers=HEADERS)
        assert resp.status_code == 200
        numbers = [i["number"] for i in resp.json()["result"]]
        assert number in numbers

    def test_filter_by_state(self, client):
        created = client.post(
            "/api/now/table/incident",
            json={"record": {"short_description": "filter test"}},
            headers=HEADERS,
        ).json()
        number = created["record"]["number"]
        client.patch(
            f"/api/now/table/incident/{number}",
            json={"record": {"state": "6"}},
            headers=HEADERS,
        )

        resp = client.get("/api/now/table/incident?state=6", headers=HEADERS)
        assert resp.status_code == 200
        numbers = [i["number"] for i in resp.json()["result"]]
        assert number in numbers


class TestUserEndpoints:
    def test_create_and_lookup_user(self, client):
        client.post(
            "/api/now/table/sys_user",
            json={"name": "NOC Agent", "user_name": "noc.agent", "active": "true"},
            headers=HEADERS,
        )

        resp = client.get(
            "/api/now/table/sys_user?sysparm_query=name%3DNOC%20Agent&sysparm_limit=1",
            headers=HEADERS,
        )
        assert resp.status_code == 200
        assert len(resp.json()["result"]) == 1
        assert resp.json()["result"][0]["name"] == "NOC Agent"


class TestHealth:
    def test_healthz(self, client):
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
