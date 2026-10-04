from pydantic import model_validator

from app.context import get_workspace
from app.tool.python_execute import DEFAULT_TIMEOUT_SECONDS, PythonExecute


_CODE_DESCRIPTION = """Python code to execute.
# Note
1. The code should generate a comprehensive text-based report containing dataset overview, column details, basic statistics, derived metrics, timeseries comparisons, outliers, and key insights.
2. Use print() for all outputs so the analysis (including sections like 'Dataset Overview' or 'Preprocessing Results') is clearly visible and save it also
3. Save any report / processed files / each analysis result in worksapce directory: {directory}
4. Data reports need to be content-rich, including your overall analysis process and corresponding data visualization.
5. You can invode this tool step-by-step to do data analysis from summary to in-depth with data report saved also"""


class NormalPythonExecute(PythonExecute):
    """A tool for executing Python code with timeout and safety restrictions."""

    name: str = "python_execute"
    description: str = """Execute Python code for in-depth data analysis / data report(task conclusion) / other normal task without direct visualization."""
    parameters: dict = {
        "type": "object",
        "properties": {
            "code_type": {
                "description": "code type, data process / data report / others",
                "type": "string",
                "default": "process",
                "enum": ["process", "report", "others"],
            },
            "code": {
                "type": "string",
                "description": _CODE_DESCRIPTION,
            },
        },
        "required": ["code"],
    }

    @model_validator(mode="after")
    def _describe_workspace(self) -> "NormalPythonExecute":
        """Point the code description at the workspace of the current run."""
        code = self.parameters.get("properties", {}).get("code")
        if code and "{directory}" in code.get("description", ""):
            code["description"] = code["description"].format(directory=get_workspace())
        return self

    async def execute(
        self,
        code: str,
        code_type: str | None = None,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
    ):
        return await super().execute(code, timeout)
