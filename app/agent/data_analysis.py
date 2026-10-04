from pydantic import Field

from app.agent.toolcall import ToolCallAgent
from app.context import get_workspace
from app.prompt.visualization import NEXT_STEP_PROMPT, SYSTEM_PROMPT
from app.tool.chart_visualization.chart_prepare import VisualizationPrepare
from app.tool.chart_visualization.data_visualization import DataVisualization
from app.tool.chart_visualization.python_execute import NormalPythonExecute
from app.tool.terminate import Terminate
from app.tool.tool_collection import ToolCollection


class DataAnalysis(ToolCallAgent):
    """
    A data analysis agent that uses planning to solve various data analysis tasks.

    This agent extends ToolCallAgent with a comprehensive set of tools and capabilities,
    including Data Analysis, Chart Visualization, Data Report.
    """

    name: str = "Data_Analysis"
    description: str = "An analytical agent that utilizes python and data visualization tools to solve diverse data analysis tasks"

    # Formatted per instance so that each run sees its own workspace
    system_prompt: str = Field(
        default_factory=lambda: SYSTEM_PROMPT.format(directory=get_workspace())
    )
    next_step_prompt: str = NEXT_STEP_PROMPT

    max_observe: int = 15000
    max_steps: int = 20

    # Add general-purpose tools to the tool collection
    available_tools: ToolCollection = Field(
        default_factory=lambda: ToolCollection(
            NormalPythonExecute(),
            VisualizationPrepare(),
            DataVisualization(),
            Terminate(),
        )
    )
