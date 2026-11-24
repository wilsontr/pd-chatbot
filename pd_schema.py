"""Pydantic models for pd-patch validation.

Mirrors frontend/src/types.ts exactly (tests/test_pd_schema.py compares the two). Used server-side to validate
patch JSON emitted by Claude before it reaches the client.
"""

from pydantic import BaseModel, Field
from typing import Literal, get_args

# ``text`` is what the box would be created with in Pd, for every type: the object
# and its arguments ("osc~ 440", "hsl 128 15 0 127", "table wave 512"), a message's
# contents, or a comment's text. For a widget or data store it starts with the type name.
PdObjectType = Literal[
    "obj", "msg", "floatatom", "comment",
    # Named data stores
    "table", "array",
    # UI widgets
    "bng", "tgl", "hsl", "vsl", "hradio", "vradio", "nbx", "vu", "cnv",
]

PD_OBJECT_TYPES: tuple[str, ...] = get_args(PdObjectType)


class PdObject(BaseModel):
    id: str
    type: PdObjectType
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


class PdPatchDownload(PdPatch):
    """A patch sent back by the browser for download.

    The viewer renders blocks over the model's 15-box limit, so this accepts
    them too, with a cap on both lists because it is a public request body.
    """

    objects: list[PdObject] = Field(min_length=1, max_length=200)
    connections: list[PdConnection] = Field(max_length=1000)
