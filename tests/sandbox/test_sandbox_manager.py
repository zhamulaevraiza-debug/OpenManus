import asyncio
import os
import tempfile
from typing import AsyncGenerator

import pytest
import pytest_asyncio

from app.sandbox.core.manager import SandboxManager


pytestmark = [pytest.mark.docker, pytest.mark.asyncio]


@pytest_asyncio.fixture(scope="function")
async def manager() -> AsyncGenerator[SandboxManager, None]:
    """Creates a sandbox manager instance.

    Uses function scope to ensure each test case has its own manager instance.
    """
    manager = SandboxManager(max_sandboxes=2, idle_timeout=60, cleanup_interval=30)
    try:
        yield manager
    finally:
        # Ensure all resources are cleaned up
        await manager.cleanup()


@pytest.fixture
def temp_file():
    """Creates a temporary test file."""
    with tempfile.NamedTemporaryFile(mode="w+", delete=False) as f:
        f.write("test content")
        path = f.name
    try:
        yield path
    finally:
        if os.path.exists(path):
            os.unlink(path)


async def test_create_sandbox(manager):
    """Tests sandbox creation."""
    # Create default sandbox
    sandbox_id = await manager.create_sandbox()
    assert sandbox_id in manager._sandboxes
    assert sandbox_id in manager._last_used

    # Verify sandbox functionality
    sandbox = await manager.get_sandbox(sandbox_id)
    result = await sandbox.run_command("echo 'test'")
    assert result.strip() == "test"


async def test_max_sandboxes_limit(manager):
    """Tests maximum sandbox limit enforcement."""
    created_sandboxes = []
    try:
        # Create maximum number of sandboxes
        for _ in range(manager.max_sandboxes):
            sandbox_id = await manager.create_sandbox()
            created_sandboxes.append(sandbox_id)

        # Verify created sandbox count
        assert len(manager._sandboxes) == manager.max_sandboxes

        # Attempting to create additional sandbox should fail
        with pytest.raises(RuntimeError) as exc_info:
            await manager.create_sandbox()

        # Verify error message
        expected_message = (
            f"Maximum number of sandboxes ({manager.max_sandboxes}) reached"
        )
        assert str(exc_info.value) == expected_message

    finally:
        # Clean up all created sandboxes
        for sandbox_id in created_sandboxes:
            try:
                await manager.delete_sandbox(sandbox_id)
            except Exception as e:
                print(f"Failed to cleanup sandbox {sandbox_id}: {e}")


async def test_concurrent_creation_respects_limit(manager):
    """Concurrent creations never exceed max_sandboxes and do not deadlock."""
    results = await asyncio.gather(
        *(manager.create_sandbox() for _ in range(manager.max_sandboxes + 1)),
        return_exceptions=True,
    )
    created = [r for r in results if isinstance(r, str)]
    failures = [r for r in results if isinstance(r, Exception)]
    assert len(created) == manager.max_sandboxes
    assert len(failures) == 1 and "Maximum number of sandboxes" in str(failures[0])


async def test_get_nonexistent_sandbox(manager):
    """Tests retrieving a non-existent sandbox."""
    with pytest.raises(KeyError, match="Sandbox .* not found"):
        await manager.get_sandbox("nonexistent-id")


async def test_sandbox_cleanup(manager):
    """Tests sandbox cleanup functionality."""
    sandbox_id = await manager.create_sandbox()
    assert sandbox_id in manager._sandboxes

    await manager.delete_sandbox(sandbox_id)
    assert sandbox_id not in manager._sandboxes
    assert sandbox_id not in manager._last_used


async def test_idle_sandbox_cleanup(manager):
    """Tests automatic cleanup of idle sandboxes."""
    # Set short idle timeout
    manager.idle_timeout = 0.1

    sandbox_id = await manager.create_sandbox()
    assert sandbox_id in manager._sandboxes

    # Wait longer than idle timeout
    await asyncio.sleep(0.2)

    # Trigger cleanup
    await manager._cleanup_idle_sandboxes()
    assert sandbox_id not in manager._sandboxes


async def test_manager_cleanup(manager):
    """Tests manager cleanup functionality."""
    # Create multiple sandboxes
    sandbox_ids = []
    for _ in range(2):
        sandbox_id = await manager.create_sandbox()
        sandbox_ids.append(sandbox_id)

    # Clean up all resources
    await manager.cleanup()

    # Verify all sandboxes have been cleaned up
    assert not manager._sandboxes
    assert not manager._last_used


if __name__ == "__main__":
    pytest.main(["-v", __file__])
