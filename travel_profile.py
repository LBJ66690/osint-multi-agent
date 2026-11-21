"""Visitor profile shared by registration and profile updates."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

Region = Literal["贵阳","遵义","六盘水","安顺","毕节","铜仁","黔东南","黔南","黔西南"]
Gender = Literal["女","男","不透露"]

class VisitorProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    age: int = Field(strict=True, ge=1, le=120)
    gender: Gender
    intended_region: Region
    interests: str | None = Field(None, max_length=120)
    walking_preference: str | None = Field(None, max_length=100)
