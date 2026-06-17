import pytest


@pytest.mark.integration
def test_health_endpoint_integration(integration_client):
    response = integration_client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert data["status"] == "ok"


@pytest.mark.integration
def test_openapi_schema(integration_client):
    response = integration_client.get("/openapi.json")
    assert response.status_code == 200
    data = response.json()
    assert "openapi" in data or "info" in data


@pytest.mark.integration
def test_auth_token_success(integration_client):
    response = integration_client.post(
        "/auth/token",
        data={"username": "admin", "password": "admin123"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["token_type"] == "bearer"
    assert isinstance(data["access_token"], str)
    assert data["access_token"]


@pytest.mark.integration
def test_auth_token_failure(integration_client):
    response = integration_client.post(
        "/auth/token",
        data={"username": "admin", "password": "wrong-password"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Incorrect username or password"
