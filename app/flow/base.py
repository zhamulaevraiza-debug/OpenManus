from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Union

from pydantic import BaseModel, ConfigDict, InstanceOf

from app.agent.base import BaseAgent
from app.agent.registry import AgentSpec


# A flow member: a ready agent instance (reused for every step), or a registry spec
# from which the flow creates a fresh agent whenever it needs one.
FlowAgent = Union[BaseAgent, InstanceOf[AgentSpec]]


class BaseFlow(BaseModel, ABC):
    """Base class for execution flows supporting multiple agents"""

    agents: Dict[str, FlowAgent]
    tools: Optional[List] = None
    primary_agent_key: Optional[str] = None

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def __init__(
        self,
        agents: Union[FlowAgent, List[FlowAgent], Dict[str, FlowAgent]],
        **data,
    ):
        # Handle different ways of providing agents
        if isinstance(agents, (BaseAgent, AgentSpec)):
            agents_dict = {"default": agents}
        elif isinstance(agents, list):
            agents_dict = {f"agent_{i}": agent for i, agent in enumerate(agents)}
        else:
            agents_dict = agents

        # If primary agent not specified, use first agent
        primary_key = data.get("primary_agent_key")
        if not primary_key and agents_dict:
            primary_key = next(iter(agents_dict))
            data["primary_agent_key"] = primary_key

        # Set the agents dictionary
        data["agents"] = agents_dict

        # Initialize using BaseModel's init
        super().__init__(**data)

    @property
    def primary_agent(self) -> Optional[FlowAgent]:
        """Get the primary agent for the flow"""
        return self.agents.get(self.primary_agent_key)

    def get_agent(self, key: str) -> Optional[FlowAgent]:
        """Get a specific agent by key"""
        return self.agents.get(key)

    def add_agent(self, key: str, agent: FlowAgent) -> None:
        """Add a new agent to the flow"""
        self.agents[key] = agent

    @abstractmethod
    async def execute(self, input_text: str) -> str:
        """Execute the flow with given input"""
