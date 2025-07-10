import logging
from pathlib import Path

from pregdos.model_ct import CTModel
from pregdos.model_rtstruct import RTStruct
from pregdos.topas_text import TopasText


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
        lines.append(TopasText.setup())
        lines.append(TopasText.world_setup())
        lines.append(TopasText.geometry())
        lines.append(TopasText.scorer_setup_DICOM())
        lines.append(TopasText.footer())
        topas_string = "\n".join(lines)
        return topas_string
