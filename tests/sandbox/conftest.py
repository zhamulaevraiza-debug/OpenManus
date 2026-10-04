"""Fixtures for the Docker sandbox tests."""

import pytest


SANDBOX_IMAGE = "python:3.12-slim"


@pytest.fixture(scope="session", autouse=True)
def sandbox_image():
    """Make sure the default sandbox image is present (containers are not pulled)."""
    import docker
    from docker.errors import DockerException, ImageNotFound

    try:
        client = docker.from_env(timeout=5)
    except DockerException:
        pytest.skip("Docker daemon is not available")
    try:
        client.images.get(SANDBOX_IMAGE)
    except ImageNotFound:
        client.images.pull(SANDBOX_IMAGE)
    finally:
        client.close()
    return SANDBOX_IMAGE
