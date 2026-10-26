"""The base of every model hitlcad reads and writes: what plugins' members derive from too."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Model(BaseModel):
    # Serialized, every field is present: response schemas (and the panel's generated types) say so.
    model_config = ConfigDict(extra="forbid", json_schema_serialization_defaults_required=True)
