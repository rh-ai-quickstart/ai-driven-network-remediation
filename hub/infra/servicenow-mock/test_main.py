"""Unit tests for the ServiceNow mock server.

Run via:
    cd hub/infra/servicenow-mock && uv sync --group dev && uv run pytest

Also included in: make unit-tests
"""

import base64

import pytest
from fastapi.testclient import TestClient

import main

AUTH_HEADER = {"Authorization": "Basic " + base64.b64encode(b"admin:admin").decode()}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main, "MOCK_USERNAME", "admin")
    monkeypatch.setattr(main, "MOCK_PASSWORD", "admin")
    return TestClient(main.app)


class TestAuth:
    def test_missing_auth_returns_401(self, client):
        resp = client.post(
            "/api/now/table/incident",
            json={"short_description": "test"},
        )
        assert resp.status_code == 401

    def test_wrong_credentials_returns_401(self, client):
        bad = {"Authorization": "Basic " + base64.b64encode(b"admin:wrong").decode()}
        resp = client.post(
            "/api/now/table/incident",
            json={"short_description": "test"},
            headers=bad,
        )
        assert resp.status_code == 401

    def test_valid_credentials_pass(self, client):
        resp = client.post(
            "/api/now/table/incident",
            json={"short_description": "test"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 201

    def test_valid_api_key_passes(self, monkeypatch, client):
        monkeypatch.setenv("SERVICENOW_API_KEY", "demo-api-key-2026")
        resp = client.post(
            "/api/now/table/incident",
            json={"short_description": "test"},
            headers={"x-sn-apikey": "demo-api-key-2026"},
        )
        assert resp.status_code == 201

    def test_api_key_wins_when_both_auth_methods_present(self, monkeypatch, client):
        monkeypatch.setenv("SERVICENOW_API_KEY", "demo-api-key-2026")
        bad_basic = {"Authorization": "Basic " + base64.b64encode(b"admin:wrong").decode()}
        resp = client.post(
            "/api/now/table/incident",
            json={"short_description": "test"},
            headers={**bad_basic, "x-sn-apikey": "demo-api-key-2026"},
        )
        assert resp.status_code == 201


class TestCreateIncident:
    def test_returns_result_with_number_and_sys_id(self, client):
        resp = client.post(
            "/api/now/table/incident",
            json={"short_description": "Pod crash", "priority": "2"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "result" in data
        assert data["result"]["number"].startswith("INC")
        assert len(data["result"]["sys_id"]) == 32
        assert data["result"]["short_description"] == "Pod crash"
        assert data["result"]["priority"] == "2"
        assert data["result"]["state"] == "1"

    def test_increments_ticket_numbers(self, client):
        r1 = client.post(
            "/api/now/table/incident",
            json={"short_description": "first"},
            headers=AUTH_HEADER,
        )
        r2 = client.post(
            "/api/now/table/incident",
            json={"short_description": "second"},
            headers=AUTH_HEADER,
        )
        n1 = int(r1.json()["result"]["number"][3:])
        n2 = int(r2.json()["result"]["number"][3:])
        assert n2 > n1


class TestGetIncident:
    def test_query_by_number(self, client):
        created = client.post(
            "/api/now/table/incident",
            json={"short_description": "to retrieve", "priority": "1"},
            headers=AUTH_HEADER,
        ).json()
        number = created["result"]["number"]

        resp = client.get(
            f"/api/now/table/incident?sysparm_query=number%3D{number}&sysparm_limit=1",
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 200
        results = resp.json()["result"]
        assert len(results) == 1
        assert results[0]["short_description"] == "to retrieve"
        assert results[0]["priority"] == "1"

    def test_query_nonexistent_returns_empty(self, client):
        resp = client.get(
            "/api/now/table/incident?sysparm_query=number%3DINC9999999&sysparm_limit=1",
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 200
        assert resp.json()["result"] == []


class TestUpdateIncident:
    def test_patch_state_by_sys_id(self, client):
        created = client.post(
            "/api/now/table/incident",
            json={"short_description": "to update"},
            headers=AUTH_HEADER,
        ).json()
        sys_id = created["result"]["sys_id"]

        resp = client.patch(
            f"/api/now/table/incident/{sys_id}",
            json={"state": "2"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 200
        assert resp.json()["result"]["state"] == "2"

    def test_patch_work_notes(self, client):
        created = client.post(
            "/api/now/table/incident",
            json={"short_description": "work notes test"},
            headers=AUTH_HEADER,
        ).json()
        sys_id = created["result"]["sys_id"]

        resp = client.patch(
            f"/api/now/table/incident/{sys_id}",
            json={"work_notes": "note 1"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 200
        assert resp.json()["result"]["work_notes"] == "note 1"

    def test_patch_nonexistent_returns_404(self, client):
        resp = client.patch(
            "/api/now/table/incident/nonexistent-sys-id",
            json={"state": "6"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 404


class TestListIncidents:
    def test_list_all(self, client):
        created = client.post(
            "/api/now/table/incident",
            json={"short_description": "list test", "priority": "3"},
            headers=AUTH_HEADER,
        ).json()
        number = created["result"]["number"]

        resp = client.get("/api/now/table/incident", headers=AUTH_HEADER)
        assert resp.status_code == 200
        numbers = [i["number"] for i in resp.json()["result"]]
        assert number in numbers

    def test_filter_by_sysparm_query(self, client):
        created = client.post(
            "/api/now/table/incident",
            json={"short_description": "filter test"},
            headers=AUTH_HEADER,
        ).json()
        sys_id = created["result"]["sys_id"]

        client.patch(
            f"/api/now/table/incident/{sys_id}",
            json={"state": "6"},
            headers=AUTH_HEADER,
        )

        number = created["result"]["number"]
        resp = client.get(
            "/api/now/table/incident?sysparm_query=state%3D6",
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 200
        numbers = [i["number"] for i in resp.json()["result"]]
        assert number in numbers

    def test_sysparm_fields(self, client):
        client.post(
            "/api/now/table/incident",
            json={"short_description": "fields test"},
            headers=AUTH_HEADER,
        )
        resp = client.get(
            "/api/now/table/incident?sysparm_fields=number,short_description",
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 200
        for item in resp.json()["result"]:
            assert "number" in item
            assert "sys_id" not in item


class TestUserEndpoints:
    def test_create_and_lookup_user(self, client):
        client.post(
            "/api/now/table/sys_user",
            json={"name": "NOC Agent", "user_name": "noc.agent", "active": "true"},
            headers=AUTH_HEADER,
        )

        resp = client.get(
            "/api/now/table/sys_user?sysparm_query=name%3DNOC%20Agent&sysparm_limit=1",
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 200
        assert len(resp.json()["result"]) == 1
        assert resp.json()["result"][0]["name"] == "NOC Agent"


class TestHealth:
    def test_healthz(self, client):
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
