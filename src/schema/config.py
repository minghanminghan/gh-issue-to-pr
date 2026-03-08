from typing import TypedDict


class AgentConfig(TypedDict):
    """Customizable config by user to be applied on top of default settings"""

    model_name: str | None
    max_steps: int | None
    budget: float | None
    model_api_key: str | None
    model_endpoint: str | None
