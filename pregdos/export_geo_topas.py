import logging
import datetime
import getpass  # used for recording the user who generated the file
from pathlib import Path

from pregdos.__version__ import __version__
from pregdos.model_ct import CTModel
from pregdos.model_rtstruct import RTStruct


logger = logging.getLogger(__name__)


def export_geo(ct: CTModel, rs: RTStruct, output_path: Path) -> None:
    content = TopasGeo.generate(ct, rs)
    output_path.write_text(content)
    logger.info(f"Wrote Topas geometry file: {output_path.resolve()}")


class TopasGeo:
    @staticmethod
    def generate(ct: CTModel, rs: RTStruct):
        """
        Export the CT and RTStruct models to a Topas-compatible geometry file.
        """
        lines = []
        lines.append("# Topas geometry file\n")
        lines.append(_topas_footer())

        topas_string = "\n".join(lines)
        return topas_string


def _topas_footer() -> str:
    "Add a footer to the topas file with generation date and username."

    lines = [
        "\n",
        f"# Generated {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} by user '{getpass.getuser()}'" +
        f" using dicomfix {__version__}",
        "# https://github.com/nbassler/dicomfix"]

    return "\n".join(lines)
