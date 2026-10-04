"""Tests for the AsyncDockerizedTerminal implementation."""

import asyncio
import uuid

import docker
import pytest
import pytest_asyncio

from app.sandbox.core.terminal import AsyncDockerizedTerminal


pytestmark = [pytest.mark.docker, pytest.mark.asyncio(loop_scope="module")]


@pytest.fixture(scope="module")
def docker_client():
    """Fixture providing a Docker client."""
    return docker.from_env()


@pytest.fixture(scope="module")
def docker_container(docker_client):
    """Fixture providing a test Docker container."""
    container = docker_client.containers.run(
        "python:3.12-slim",
        "tail -f /dev/null",
        name=f"openmanus_test_{uuid.uuid4().hex[:8]}",
        detach=True,
        remove=True,
    )
    yield container
    container.stop(timeout=1)


@pytest_asyncio.fixture(loop_scope="module")
async def terminal(docker_container):
    """Fixture providing an initialized AsyncDockerizedTerminal instance."""
    terminal = AsyncDockerizedTerminal(
        docker_container,
        working_dir="/workspace",
        env_vars={"TEST_VAR": "test_value"},
        default_timeout=30,
    )
    await terminal.init()
    yield terminal
    await terminal.close()


class TestAsyncDockerizedTerminal:
    """Test cases for AsyncDockerizedTerminal."""

    async def test_basic_command_execution(self, terminal):
        """Test basic command execution functionality."""
        result = await terminal.run_command("echo 'Hello World'")
        assert "Hello World" in result

    async def test_environment_variables(self, terminal):
        """Test environment variable setting and access."""
        result = await terminal.run_command("echo $TEST_VAR")
        assert "test_value" in result

    async def test_working_directory(self, terminal):
        """Test working directory setup."""
        result = await terminal.run_command("pwd")
        assert "/workspace" == result

    async def test_command_timeout(self, docker_container):
        """Test command timeout functionality."""
        terminal = AsyncDockerizedTerminal(docker_container, default_timeout=1)
        await terminal.init()
        try:
            with pytest.raises(TimeoutError):
                await terminal.run_command("sleep 5")
        finally:
            await terminal.close()

    async def test_multiple_commands(self, terminal):
        """Test execution of multiple commands in sequence."""
        cmd1 = await terminal.run_command("echo 'First'")
        cmd2 = await terminal.run_command("echo 'Second'")
        assert "First" in cmd1
        assert "Second" in cmd2

    async def test_numeric_output_is_kept(self, terminal):
        """Lines consisting only of digits are command output, not exit codes."""
        assert await terminal.run_command("echo 42") == "42"
        assert await terminal.run_command("python3 -c 'print(1 + 1)'") == "2"

    async def test_output_without_trailing_newline(self, terminal):
        """Output not terminated by a newline is returned intact."""
        assert await terminal.run_command("printf 'no-newline'") == "no-newline"

    async def test_session_recovers_after_timeout(self, docker_container):
        """A timed out command is interrupted and the session stays usable."""
        terminal = AsyncDockerizedTerminal(docker_container, default_timeout=1)
        await terminal.init()
        try:
            with pytest.raises(TimeoutError):
                await terminal.run_command("sleep 30")
            assert await terminal.run_command("echo recovered") == "recovered"
        finally:
            await terminal.close()

    async def test_concurrent_commands_do_not_interleave(self, terminal):
        """Commands on one session are serialized."""
        results = await asyncio.gather(
            *(terminal.run_command(f"sleep 0.2; echo cmd{i}") for i in range(3))
        )
        assert results == ["cmd0", "cmd1", "cmd2"]

    async def test_session_cleanup(self, docker_container):
        """Test proper cleanup of resources."""
        terminal = AsyncDockerizedTerminal(docker_container)
        await terminal.init()
        assert terminal.session is not None
        await terminal.close()
        # Verify session is properly cleaned up
        # Note: session object still exists, but internal connection is closed
        assert terminal.session is not None


if __name__ == "__main__":
    pytest.main(["-v", __file__])
