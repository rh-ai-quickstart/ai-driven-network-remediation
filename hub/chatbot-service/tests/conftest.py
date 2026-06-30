import pytest
from chatbot_service import app
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    return TestClient(app)
