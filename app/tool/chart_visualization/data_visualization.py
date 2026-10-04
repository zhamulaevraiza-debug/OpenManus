import asyncio
import json
import os
from pathlib import Path
from typing import Any, Dict, Hashable, List, Optional

import pandas as pd
from pydantic import Field, model_validator

from app.context import get_workspace, resolve_in_workspace
from app.llm import LLM
from app.logger import logger
from app.tool.base import BaseTool
from app.utils.proc import run_process


CHART_TOOL_DIR = Path(__file__).resolve().parent
RESULT_MARKER = "__VMIND_RESULT__"
RENDER_TIMEOUT_SECONDS = 300
MAX_PARALLEL_RENDERS = 2
MAX_NODE_OUTPUT_BYTES = 1_000_000


def _node_command() -> List[str]:
    """Command running chartVisualize.ts (local ts-node when installed)."""
    local = CHART_TOOL_DIR / "node_modules" / ".bin" / "ts-node"
    runner = [str(local)] if local.exists() else ["npx", "ts-node"]
    return [*runner, "--transpile-only", "src/chartVisualize.ts"]


def _node_env() -> Dict[str, str]:
    """Extra environment for the renderer: HOME in the workspace, browser location."""
    env = {"HOME": str(get_workspace())}
    for name in ("PUPPETEER_EXECUTABLE_PATH", "PUPPETEER_CACHE_DIR"):
        if os.environ.get(name):
            env[name] = os.environ[name]
    if "PUPPETEER_CACHE_DIR" not in env and "PUPPETEER_EXECUTABLE_PATH" not in env:
        # Puppeteer looks for its browser under $HOME; keep the server's download.
        default_cache = Path.home() / ".cache" / "puppeteer"
        if default_cache.is_dir():
            env["PUPPETEER_CACHE_DIR"] = str(default_cache)
    return env


def _parse_node_result(stdout: str) -> Optional[dict]:
    for line in reversed(stdout.splitlines()):
        if line.startswith(RESULT_MARKER):
            return json.loads(line[len(RESULT_MARKER) :])
    return None


def _load_csv_records(path: Path) -> str:
    df = pd.read_csv(path, encoding="utf-8")
    df = df.astype(object)
    df = df.where(pd.notnull(df), None)
    return df.to_json(orient="records", force_ascii=False)


def _load_json(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


class DataVisualization(BaseTool):
    name: str = "data_visualization"
    description: str = """Visualize statistical chart or Add insights in chart with JSON info from visualization_preparation tool. You can do steps as follows:
1. Visualize statistical chart
2. Choose insights into chart based on step 1 (Optional)
Outputs:
1. Charts (png/html)
2. Charts Insights (.md)(Optional)"""
    parameters: dict = {
        "type": "object",
        "properties": {
            "json_path": {
                "type": "string",
                "description": """file path of json info with ".json" in the end (relative to the workspace)""",
            },
            "output_type": {
                "description": "Rendering format (html=interactive)",
                "type": "string",
                "default": "html",
                "enum": ["png", "html"],
            },
            "tool_type": {
                "description": "visualize chart or add insights",
                "type": "string",
                "default": "visualization",
                "enum": ["visualization", "insight"],
            },
            "language": {
                "description": "english(en) / chinese(zh)",
                "type": "string",
                "default": "en",
                "enum": ["zh", "en"],
            },
        },
        "required": ["json_path"],
    }
    llm: LLM = Field(default_factory=LLM, description="Language model instance")

    @model_validator(mode="after")
    def initialize_llm(self):
        """Initialize llm with default settings if not provided."""
        if self.llm is None or not isinstance(self.llm, LLM):
            self.llm = LLM(config_name=self.name.lower())
        return self

    def get_file_path(
        self,
        json_info: list[dict[str, str]],
        path_str: str,
        directory: Optional[Path] = None,
    ) -> list[Path]:
        """Resolve the ``path_str`` entries of ``json_info`` inside the workspace.

        Paths are tried relative to the workspace first, then relative to
        ``directory`` (also inside the workspace).
        """
        res = []
        for item in json_info:
            raw = item[path_str]
            candidates = [resolve_in_workspace(raw)]
            if directory is not None and not Path(raw).is_absolute():
                candidates.append(resolve_in_workspace(directory / raw))
            existing = next((path for path in candidates if path.exists()), None)
            if existing is None:
                raise FileNotFoundError(f"No such file or directory: {raw}")
            res.append(existing)
        return res

    def success_output_template(self, result: list[dict[str, str]]) -> str:
        content = ""
        if len(result) == 0:
            return "Is EMPTY!"
        for item in result:
            content += f"""## {item['title']}\nChart saved in: {item['chart_path']}"""
            if "insight_path" in item and item["insight_path"] and "insight_md" in item:
                content += "\n" + item["insight_md"]
            else:
                content += "\n"
        return f"Chart Generated Successful!\n{content}"

    async def _render_all(self, jobs: List[Dict[str, Any]]) -> List[dict]:
        """Run the renderer for every job, at most MAX_PARALLEL_RENDERS at a time."""
        semaphore = asyncio.Semaphore(MAX_PARALLEL_RENDERS)

        async def render(job: Dict[str, Any]) -> dict:
            async with semaphore:
                return await self.invoke_vmind(**job)

        return await asyncio.gather(*(render(job) for job in jobs))

    async def data_visualization(
        self, json_info: list[dict[str, str]], output_type: str, language: str
    ) -> dict:
        data_list = []
        csv_file_path = self.get_file_path(json_info, "csvFilePath")
        for index, item in enumerate(json_info):
            data_list.append(
                {
                    "file_name": csv_file_path[index].stem,
                    "dict_data": await asyncio.to_thread(
                        _load_csv_records, csv_file_path[index]
                    ),
                    "chartTitle": item["chartTitle"],
                }
            )
        results = await self._render_all(
            [
                dict(
                    dict_data=item["dict_data"],
                    chart_description=item["chartTitle"],
                    file_name=item["file_name"],
                    output_type=output_type,
                    task_type="visualization",
                    language=language,
                )
                for item in data_list
            ]
        )
        error_list = []
        success_list = []
        for index, result in enumerate(results):
            csv_path = csv_file_path[index]
            if "error" in result and "chart_path" not in result:
                error_list.append(f"Error in {csv_path}: {result['error']}")
            else:
                success_list.append(
                    {
                        **result,
                        "title": json_info[index]["chartTitle"],
                    }
                )
        if len(error_list) > 0:
            errors = "\n".join(error_list)
            return {
                "observation": f"# Error chart generated{errors}\n{self.success_output_template(success_list)}",
                "success": False,
            }
        else:
            return {"observation": f"{self.success_output_template(success_list)}"}

    async def add_insighs(
        self, json_info: list[dict[str, str]], output_type: str
    ) -> dict:
        data_list = []
        chart_file_path = self.get_file_path(
            json_info, "chartPath", get_workspace() / "visualization"
        )
        for index, item in enumerate(json_info):
            if "insights_id" in item:
                data_list.append(
                    {
                        "file_name": chart_file_path[index].name.replace(
                            f".{output_type}", ""
                        ),
                        "insights_id": item["insights_id"],
                    }
                )
        results = await self._render_all(
            [
                dict(
                    insights_id=item["insights_id"],
                    file_name=item["file_name"],
                    output_type=output_type,
                    task_type="insight",
                )
                for item in data_list
            ]
        )
        error_list = []
        success_list = []
        for index, result in enumerate(results):
            chart_path = str(chart_file_path[index])
            if "error" in result and "chart_path" not in result:
                error_list.append(f"Error in {chart_path}: {result['error']}")
            else:
                success_list.append(chart_path)
        success_template = (
            f"# Charts Update with Insights\n{','.join(success_list)}"
            if len(success_list) > 0
            else ""
        )
        if len(error_list) > 0:
            errors = "\n".join(error_list)
            return {
                "observation": f"# Error in chart insights:{errors}\n{success_template}",
                "success": False,
            }
        else:
            return {"observation": f"{success_template}"}

    async def execute(
        self,
        json_path: str,
        output_type: str | None = "html",
        tool_type: str | None = "visualization",
        language: str | None = "en",
    ) -> dict:
        try:
            logger.info(f"📈 data_visualization with {json_path} in: {tool_type} ")
            json_info = await asyncio.to_thread(
                _load_json, resolve_in_workspace(json_path)
            )
            if tool_type == "visualization":
                return await self.data_visualization(json_info, output_type, language)
            else:
                return await self.add_insighs(json_info, output_type)
        except Exception as e:
            return {
                "observation": f"Error: {e}",
                "success": False,
            }

    async def invoke_vmind(
        self,
        file_name: str,
        output_type: str,
        task_type: str,
        insights_id: list[str] = None,
        dict_data: list[dict[Hashable, Any]] = None,
        chart_description: str = None,
        language: str = "en",
    ) -> dict:
        """Render one chart with the Node.js VMind helper (killed on timeout)."""
        llm_config = {
            "base_url": self.llm.base_url,
            "model": self.llm.model,
            "api_key": self.llm.api_key,
        }
        vmind_params = {
            "llm_config": llm_config,
            "user_prompt": chart_description,
            "dataset": dict_data,
            "file_name": file_name,
            "output_type": output_type,
            "insights_id": insights_id,
            "task_type": task_type,
            "directory": str(get_workspace()),
            "language": language,
        }
        try:
            result = await run_process(
                *_node_command(),
                stdin_data=json.dumps(vmind_params, ensure_ascii=False).encode("utf-8"),
                timeout=RENDER_TIMEOUT_SECONDS,
                output_limit=MAX_NODE_OUTPUT_BYTES,
                cwd=CHART_TOOL_DIR,
                extra_env=_node_env(),
            )
        except OSError as e:
            return {"error": f"Failed to start the chart renderer: {e}"}
        if result.timed_out:
            return {
                "error": f"Chart rendering timed out after {RENDER_TIMEOUT_SECONDS}s"
            }
        try:
            parsed = _parse_node_result(result.stdout)
        except json.JSONDecodeError as e:
            return {"error": f"Invalid renderer output: {e}"}
        if parsed is None:
            return {"error": f"Node.js Error: {result.stderr or result.stdout}"}
        return parsed
