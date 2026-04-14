from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, Field


class AppConfig(BaseModel):
    project_root: Path = Field(default_factory=lambda: Path(__file__).resolve().parents[1])
    data_dir: Path | None = None
    outputs_dir: Path | None = None
    openai_api_key: str | None = None
    default_llm_provider: str = "openai"
    default_llm_model: str = "gpt-4.1"

    def model_post_init(self, __context: object) -> None:
        if self.data_dir is None:
            self.data_dir = self.project_root / "data"
        if self.outputs_dir is None:
            self.outputs_dir = self.project_root / "outputs"


def load_config() -> AppConfig:
    project_root = Path(__file__).resolve().parents[1]
    return AppConfig(
        project_root=project_root,
        data_dir=project_root / "data",
        outputs_dir=project_root / "outputs",
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        default_llm_provider=os.getenv("KONKRD_LLM_PROVIDER", "openai"),
        default_llm_model=os.getenv("KONKRD_LLM_MODEL", "gpt-4.1"),
    )
