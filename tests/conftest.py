"""Shared pytest configuration for all test suites."""

import os

import pytest


# Tests never write log files (must be set before app.logger is imported).
os.environ.setdefault("OPENMANUS_LOG_DIR", "")


def _docker_available() -> bool:
    try:
        import docker

        client = docker.from_env(timeout=3)
        try:
            client.ping()
        finally:
            client.close()
    except Exception:
        return False
    return True


def pytest_collection_modifyitems(config, items):
    """Skip tests marked ``docker`` when no Docker daemon is reachable."""
    docker_items = [item for item in items if item.get_closest_marker("docker")]
    if not docker_items or _docker_available():
        return
    skip = pytest.mark.skip(reason="Docker daemon is not available")
    for item in docker_items:
        item.add_marker(skip)
