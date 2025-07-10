import logging
import datetime
import getpass  # used for recording the user who generated the file
from pathlib import Path

from pregdos.__version__ import __version__
from pregdos.model_ct import CTModel
from pregdos.model_rtstruct import RTStruct


logger = logging.getLogger(__name__)

# TODO: implement CT setup and Water phantom setup.


class TopasGeo:
    @staticmethod
    def export(ct: CTModel, rs: RTStruct, fout: Path):
        """
        Export the CT and RTStruct models to a Topas-compatible geometry file.
        """

        logger.info(f"Exporting geometry to {fout}")

        with open(fout, 'w') as f:
            # Write header
            f.write("# Topas geometry file\n")
            f.write(_topas_footer())
        logger.info(f"Topas input file written to '{fout}'")


def _topas_footer() -> str:
    "Add a footer to the topas file with generation date and username."

    lines = [
        "\n",
        f"# Generated {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} by user '{getpass.getuser()}'" +
        f" using dicomfix {__version__}",
        "# https://github.com/nbassler/dicomfix"]

    return "\n".join(lines)
