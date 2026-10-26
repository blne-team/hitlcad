"""The gear connector: a spur gear's teeth, measured on the part, meshed by the gear relations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from hitlcad.core import frames as fr
from hitlcad.core.structure import Connector, Measured, Ref, Tolerances

if TYPE_CHECKING:
    from hitlcad.geometry.features import Features


class GearConnector(Connector):
    """A spur gear. Origin at its centre at the bottom of the teeth, Z along its axis. Measured: across its face
    width, its tip diameter, m(z + 2) (m(z − 2) for an internal gear), and its teeth; its mesh is verified by its
    relations and by interference. The nominal values name parameters or interface values."""

    kind: Literal["gear"]
    teeth: Ref
    module: Ref
    width: Ref
    internal: bool = False
    example = {"kind": "gear", "teeth": "sun_teeth", "module": "module", "width": "face_width"}

    def nominal_refs(self) -> dict[str, Ref]:
        return {"teeth": self.teeth, "module": self.module, "width": self.width}

    def fixed_nominals(self) -> dict[str, float]:
        return {"internal": float(self.internal)}

    def measure(self, features: Features, frame: fr.Frame, tol: Tolerances, nominals: dict[str, float]) -> Measured:
        return features.gear(frame, tol, module=nominals["module"], teeth=round(nominals["teeth"]),
                             width=nominals["width"], internal=self.internal)  # fmt: skip
