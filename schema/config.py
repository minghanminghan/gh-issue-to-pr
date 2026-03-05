from dataclasses import dataclass

@dataclass
class AgentConfig:
    """Customizable config by user to be applied on top of default settings"""
    model_name: str | None
    max_steps: int | None
    # TODO: extend (budget)