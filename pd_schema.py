"""Pydantic models for pd-patch validation.

Mirrors frontend/src/types.ts exactly. Used server-side to validate
patch JSON emitted by Claude before it reaches the client.
"""

from pydantic import BaseModel, Field
from typing import Literal


class PdObject(BaseModel):
    id: str
    type: Literal["obj", "msg", "floatatom", "comment"]
    text: str
    inlets: int = Field(ge=0)
    outlets: int = Field(ge=0)


class PdConnection(BaseModel):
    srcId: str
    srcOutlet: int = Field(ge=0)
    dstId: str
    dstInlet: int = Field(ge=0)


class PdPatch(BaseModel):
    objects: list[PdObject] = Field(min_length=1, max_length=15)
    connections: list[PdConnection]
