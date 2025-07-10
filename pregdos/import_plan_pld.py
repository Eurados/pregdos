import logging
from pathlib import Path

from pregdos.beam_model import get_fwhm
from pregdos.model_plan import Plan, Field, Layer, Spot

logger = logging.getLogger(__name__)


def load_plan_pld(file_pld: Path, scaling=1.0) -> Plan:
    """
    Load a IBA-style PLD-file.

    file_pld : a file pointer to a .pld file, opened for reading.
    Here we assume there is only a single field in every .pld file.
    """
    logging.warning("IBA_PLD reader not tested yet.")
    eps = 1.0e-10

    current_plan = Plan()
    myfield = Field()  # avoid collision with dataclasses.field
    current_plan.fields = [myfield]
    current_plan.n_fields = 1

    # # TODO: needs beam model to be applied for spot parameters and MU scaling.
    # # For now, we simply assume a constant factor for the number of particles per MU (which is not correct).
    # # p.factor holds the number of particles * dE/dx / MU = some constant
    # # p.factor = 8.106687e7  # Calculated Nov. 2016 from Brita's 32 Gy plan. (no dE/dx)
    # current_plan.factor = 5.1821e8  # protons per (MU/dEdx), Estimated calculation Apr. 2017 from Brita's 32 Gy plan.

    # # currently scaling is treated equal at plan and field level. This is for future use.
    # current_plan.scaling = scaling
    # myfield.scaling = scaling

    pldlines = file_pld.read_text().split('\n')
    pldlen = len(pldlines)
    logger.info("Read %d lines of data.", pldlen)

    myfield.layers = []

    # First line in PLD file contains both plan and field data
    tokens = pldlines[0].split(",")
    current_plan.patient_id = tokens[1].strip()
    current_plan.patient_name = tokens[2].strip()
    current_plan.patient_initals = tokens[3].strip()
    current_plan.patient_firstname = tokens[4].strip()
    current_plan.plan_label = tokens[5].strip()
    current_plan.beam_name = tokens[6].strip()
    myfield.cmu = float(tokens[7].strip())          # total amount of MUs in this field
    myfield.pld_csetweight = float(tokens[8].strip())
    myfield.n_layers = int(tokens[9].strip())       # number of layers

    espread = 0.0  # will be set by beam model

    i = 1
    while i < pldlen:
        line = pldlines[i]
        if "Layer" in line:
            tokens = line.split(",")

            spotsize_sigma = float(tokens[1].strip())
            spotsize_fwhm = get_fwhm(spotsize_sigma)  # single value in mm

            energy_nominal = float(tokens[2].strip())
            cmu = float(tokens[3].strip())
            nspots_expected = int(tokens[4].strip())

            nrepaint = int(tokens[5].strip()) if len(tokens) > 5 else 0

            elements = []
            j = i + 1
            while j < pldlen and "Element" in pldlines[j]:
                elements.append(pldlines[j])
                j += 1

            spots = []
            for el in elements:
                el_tokens = el.split(",")
                _x = float(el_tokens[1].strip())
                _y = float(el_tokens[2].strip())
                _mu = float(el_tokens[3].strip())

                if abs(_x) < eps:
                    _x = 0.0
                if abs(_y) < eps:
                    _y = 0.0
                if _mu < eps:
                    _mu = 0.0

                # Skip empty spots
                if _mu > 0.0:
                    spots.append(Spot(
                        x=_x,
                        y=_y,
                        mu=_mu,
                        size_x=spotsize_fwhm,
                        size_y=spotsize_fwhm
                    ))

            # check if expected number of spots is correct
            if len(spots) != nspots_expected:
                logger.warning("Expected %d spots, but found %d in layer %d at energy %.2f MeV",
                               nspots_expected, len(spots), len(myfield.layers), energy_nominal)

            layer = Layer(
                spots=spots,
                energy_nominal=energy_nominal,
                energy_measured=energy_nominal,
                espread=espread,
                cum_mu=cmu,
                n_spots=len(spots),
                repaint=nrepaint,
                mu_to_part_coef=0.0  # to be set by beam model later
            )

            current_plan.fields[0].layers.append(layer)
            logger.debug("Appended layer %d with %d spots", len(current_plan.fields[0].layers), len(spots))

            i = j  # move to next layer or EOF
        else:
            i += 1

    return current_plan
