"""Chart visualization tools: schema, workspace confinement and the Node renderer."""

import json
import os
import shutil
from pathlib import Path

import pytest

from app.exceptions import WorkspaceViolation
from app.tool.chart_visualization import (
    DataVisualization,
    NormalPythonExecute,
    VisualizationPrepare,
)
from app.tool.chart_visualization import data_visualization as dv


CHROME = Path("/opt/pw-browsers/chromium-1194/chrome-linux/chrome")
NODE_READY = (dv.CHART_TOOL_DIR / "node_modules" / ".bin" / "ts-node").exists() and (
    shutil.which("node") is not None
)


def test_schema_requires_json_path():
    assert DataVisualization().parameters["required"] == ["json_path"]


def test_python_tools_describe_the_current_workspace(run_ctx):
    description = NormalPythonExecute().parameters["properties"]["code"]["description"]
    assert str(run_ctx.workspace) in description
    assert VisualizationPrepare().name == "visualization_preparation"


def test_file_paths_are_confined_to_workspace(run_ctx, tmp_path):
    tool = DataVisualization()
    (run_ctx.workspace / "visualization").mkdir()
    (run_ctx.workspace / "visualization" / "c.html").write_text("x")
    (run_ctx.workspace / "data.csv").write_text("a\n1\n")

    resolved = tool.get_file_path(
        [{"chartPath": "c.html"}], "chartPath", run_ctx.workspace / "visualization"
    )
    assert resolved == [run_ctx.workspace / "visualization" / "c.html"]
    assert tool.get_file_path([{"p": "data.csv"}], "p") == [
        run_ctx.workspace / "data.csv"
    ]
    with pytest.raises(WorkspaceViolation):
        tool.get_file_path([{"p": "/etc/passwd"}], "p")
    with pytest.raises(FileNotFoundError):
        tool.get_file_path([{"p": "missing.csv"}], "p")


@pytest.mark.asyncio
async def test_errors_are_reported_per_chart(run_ctx, monkeypatch):
    (run_ctx.workspace / "ok.csv").write_text("x,y\na,1\n")
    (run_ctx.workspace / "bad.csv").write_text("x,y\nb,2\n")
    (run_ctx.workspace / "info.json").write_text(
        json.dumps(
            [
                {"csvFilePath": "ok.csv", "chartTitle": "Good"},
                {"csvFilePath": "bad.csv", "chartTitle": "Bad"},
            ]
        )
    )
    jobs = []

    async def fake_invoke(self, **job):
        jobs.append(job)
        if job["file_name"] == "bad":
            return {"error": "renderer exploded"}
        return {"chart_path": str(run_ctx.workspace / "visualization" / "ok.html")}

    monkeypatch.setattr(DataVisualization, "invoke_vmind", fake_invoke)
    result = await DataVisualization().execute(json_path="info.json")

    assert result["success"] is False
    assert "renderer exploded" in result["observation"]
    assert "## Good" in result["observation"]
    assert json.loads(jobs[0]["dict_data"]) == [{"x": "a", "y": 1}]


@pytest.mark.asyncio
async def test_json_path_outside_workspace_is_rejected(run_ctx):
    result = await DataVisualization().execute(json_path="/etc/passwd")
    assert result["success"] is False
    assert "Access denied" in result["observation"]


@pytest.mark.asyncio
@pytest.mark.skipif(not NODE_READY, reason="chart_visualization node_modules missing")
async def test_renderer_round_trip(run_ctx, monkeypatch):
    if CHROME.exists():
        monkeypatch.setenv("PUPPETEER_EXECUTABLE_PATH", str(CHROME))
    elif not os.environ.get("PUPPETEER_EXECUTABLE_PATH"):
        pytest.skip("no Chromium for Puppeteer")
    visualization = run_ctx.workspace / "visualization"
    visualization.mkdir()
    spec = {
        "type": "bar",
        "data": [{"id": "d", "values": [{"x": "A", "y": 3}, {"x": "B", "y": 5}]}],
        "xField": "x",
        "yField": "y",
    }
    (visualization / "demo.json").write_text(json.dumps(spec))
    tool = DataVisualization()

    empty = await tool.invoke_vmind(
        file_name="demo", output_type="png", task_type="insight", insights_id=[]
    )
    assert "No insights were selected" in empty["error"]

    rendered = await tool.invoke_vmind(
        file_name="demo", output_type="png", task_type="insight", insights_id=[1]
    )
    assert rendered == {"chart_path": str(visualization / "demo.png")}
    assert (visualization / "demo.png").read_bytes()[:4] == b"\x89PNG"
