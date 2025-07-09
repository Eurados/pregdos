from dataclasses import dataclass, field as dc_field
from typing import List, Optional, Tuple

import numpy as np
import logging

from pregdos.beam_model import BeamModel, get_fwhm
from io import StringIO
import sys

logger = logging.getLogger(__name__)


INDENT = "    "


@dataclass
class Spot:
    """Single scanned spot in a proton layer."""
    x: float
    y: float
    mu: float
    size_x: float = 0.0  # FWHM in X
    size_y: float = 0.0  # FWHM in Y

    def __repr__(self):
        return f"<Spot x={self.x:.2f} y={self.y:.2f} mu={self.mu:.4f}>"


@dataclass
class Layer:
    """
    A single energy layer in a proton field.

    Attributes:
        spots: List of Spot objects.
        energy_nominal: Nominal beam energy [MeV].
        energy_measured: Measured energy [MeV].
        espread: Energy spread [MeV].
        cum_mu: Cumulative MU.
        repaint: Number of repaintings.
        mu_to_part_coef: Conversion MU -> particles.
        is_empty: True if no MU.
        isocenter: (x, y, z) position [mm].
        gantry_angle: [deg]
        couch_angle: [deg]
        snout_position: [mm]
        sad: (x, y) source-to-axis distance [mm].
        table_position: (vert, long, lat) [mm].
        meterset_rate: MU/min (optional).
    """

    spots: List[Spot] = dc_field(default_factory=list)
    energy_nominal: float = 0.0
    energy_measured: float = 0.0
    espread: float = 0.0
    cum_mu: float = 0.0
    repaint: int = 0
    mu_to_part_coef: float = 0.0
    is_empty: bool = True

    isocenter: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    gantry_angle: float = 0.0
    couch_angle: float = 0.0
    snout_position: float = 0.0
    sad: Tuple[float, float] = (0.0, 0.0)
    table_position: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    meterset_rate: float = 0.0

    @property
    def n_spots(self) -> int:
        return len(self.spots)

    @property
    def xmin(self) -> float:
        return min((spot.x for spot in self.spots), default=0.0)

    @property
    def xmax(self) -> float:
        return max((spot.x for spot in self.spots), default=0.0)

    @property
    def ymin(self) -> float:
        return min((spot.y for spot in self.spots), default=0.0)

    @property
    def ymax(self) -> float:
        return max((spot.y for spot in self.spots), default=0.0)

    def __repr__(self):
        lines = []
        lines.append("------------------------------------------------")
        lines.append(f"Energy nominal        : {self.energy_nominal:10.4f} MeV")
        lines.append(f"Energy measured       : {self.energy_measured:10.4f} MeV")
        lines.append(f"Energy spread         : {self.espread:10.4f} MeV")
        lines.append(f"Cumulative MU         : {self.cum_mu:10.4f}")
        lines.append(f"Cumulative particles  : {getattr(self, 'cum_particles', 0.0):10.4e} (estimated)")
        lines.append(f"Number of spots       : {self.n_spots:10d}")
        lines.append("------------------------------------------------")
        lines.append(f"Spot layer min/max X  : {self.xmin:+10.4f} {self.xmax:+10.4f} mm")
        lines.append(f"Spot layer min/max Y  : {self.ymin:+10.4f} {self.ymax:+10.4f} mm")
        lines.append("------------------------------------------------")
        return "\n".join(lines)


@dataclass
class Field:
    """A field consisting of multiple energy layers."""

    layers: List[Layer] = dc_field(default_factory=list)
    dose: float = 0.0
    cum_mu: float = 0.0
    pld_csetweight: float = 0.0
    scaling: float = 1.0

    meterset_weight_final: float = 0.0
    meterset_per_weight: float = 0.0

    lateral_spreading_device_distanceX: float = 0.0
    lateral_spreading_device_distanceY: float = 0.0
    sop_instance_uid: str = ""

    @property
    def n_layers(self) -> int:
        return len(self.layers)

    @property
    def n_spots(self) -> int:
        return sum(layer.n_spots for layer in self.layers)

    @property
    def xmin(self) -> float:
        return min((layer.xmin for layer in self.layers if layer.n_spots > 0), default=0.0)

    @property
    def xmax(self) -> float:
        return max((layer.xmax for layer in self.layers if layer.n_spots > 0), default=0.0)

    @property
    def ymin(self) -> float:
        return min((layer.ymin for layer in self.layers if layer.n_spots > 0), default=0.0)

    @property
    def ymax(self) -> float:
        return max((layer.ymax for layer in self.layers if layer.n_spots > 0), default=0.0)

    @property
    def emin(self) -> float:
        """Minimum energy of all layers in this field."""
        return min(layer.energy_nominal for layer in self.layers) if self.layers else 0.0

    @property
    def emax(self) -> float:
        """Maximum energy of all layers in this field."""
        return max(layer.energy_nominal for layer in self.layers) if self.layers else 0.0

    def __repr__(self):
        """Return overview of field as a string."""

        lines = []
        lines.append(INDENT + "------------------------------------------------")
        lines.append(INDENT + f"Energy layers          : {self.n_layers:10d}")
        lines.append(INDENT + f"Total MUs              : {self.cum_mu:10.4f}")
        lines.append(INDENT + "------------------------------------------------")
        for i, layer in enumerate(self.layers):
            lines.append(INDENT + f"   Layer {i+1:3}: {layer.energy_nominal: 10.4f} MeV " + f"   {layer.n_spots:10d} spots")
        lines.append(INDENT + f"Lowest energy          : {self.emin:10.4f} MeV")
        lines.append(INDENT + f"Highest energy         : {self.emax:10.4f} MeV")
        lines.append(INDENT + "------------------------------------------------")
        lines.append(INDENT + f"Spot field min/max X   : {self.xmin:+10.4f} {self.xmax:+10.4f} mm")
        lines.append(INDENT + f"Spot field min/max Y   : {self.ymin:+10.4f} {self.ymax:+10.4f} mm")
        lines.append(INDENT + "------------------------------------------------")
        lines.append("")
        return "\n".join(lines)


@dataclass
class Plan:
    """A proton therapy plan consisting of multiple fields."""

    fields: List[Field] = dc_field(default_factory=list)
    patient_id: str = ""
    patient_name: str = ""
    patient_initials: str = ""
    patient_firstname: str = ""
    plan_label: str = ""
    beam_model: Optional[BeamModel] = None  # optional beam model class
    beam_name: str = ""
    scaling: float = 1.0
    uid: str = ""

    @property
    def n_fields(self) -> int:
        return len(self.fields)

    @property
    def n_layers(self) -> int:
        return sum(field.n_layers for field in self.fields)

    @property
    def n_spots(self) -> int:
        return sum(field.n_spots for field in self.fields)

    def apply_beammodel(self):
        """Adjust plan to beam model."""
        if self.beam_model:
            for myfield in self.fields:
                for layer in myfield.layers:
                    # calculate number of particles
                    layer.mu_to_part_coef = self.beam_model.f_ppmu(layer.energy_nominal)
                    logger.debug(f"Layer {layer.energy_nominal} MeV, mu_to_part_coef = {layer.mu_to_part_coef:.2f}")
                    layer.energy_measured = self.beam_model.f_e(layer.energy_nominal)
                    layer.espread = self.beam_model.f_espread(layer.energy_nominal)
                    layer.spotsize = np.array(
                        [self.beam_model.f_sx(layer.energy_nominal),
                            self.beam_model.f_sy(layer.energy_nominal)]) * get_fwhm(1.0)

        # set cumulative sums
        for myfield in self.fields:
            myfield.cum_particles = 0.0
            myfield.cum_mu = 0.0

            # set layer specific values
            for layer in myfield.layers:
                logger.debug("Processing layer with %d spots", layer.n_spots)
                if layer.n_spots > 0:
                    mu_list = [spot.mu for spot in layer.spots]
                    layer.cum_mu = sum(mu_list)
                    layer.is_empty = False

                    myfield.cum_particles += layer.cum_particles
                    myfield.cum_mu += layer.cum_mu

    def __repr__(self):
        """Return overview of plan as a string."""
        lines = []
        lines.append("Diagnostics:")
        lines.append("---------------------------------------------------")
        lines.append(f"Patient Name           : '{self.patient_name}'       [{self.patient_initials}]")
        lines.append(f"Patient ID             : {self.patient_id}")
        lines.append(f"Plan label             : {self.plan_label}")
        lines.append(f"Plan date              : {getattr(self, 'plan_date', '')}")
        lines.append(f"Number of Fields       : {self.n_fields:2d}")

        for i, myfield in enumerate(self.fields):
            lines.append("---------------------------------------------------")
            lines.append(f"   Field                  : {i + 1:02d}/{self.n_fields:02d}:")
            # Use the field's diagnose method if it returns a string, else str()
            diagnose_str = getattr(myfield, '__str__', None)
            if callable(diagnose_str):
                lines.append(str(myfield))
            else:
                # fallback to diagnose() if it prints
                buf = StringIO()
                sys_stdout = sys.stdout
                sys.stdout = buf
                myfield.diagnose()
                sys.stdout = sys_stdout
                lines.append(buf.getvalue().strip())
            lines.append("")
        return "\n".join(lines)
