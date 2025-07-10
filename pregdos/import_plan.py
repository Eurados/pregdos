import logging
from pathlib import Path

from pregdos.model_plan import Plan
from pregdos.import_plan_pld import load_plan_pld
from pregdos.import_plan_dicom import load_plan_dicom
from pregdos.import_plan_rst import load_plan_rst

logger = logging.getLogger(__name__)


def load_plan(path: Path, **kwargs) -> Plan:
    """
    Load a treatment plan from a file (PLD, DICOM RT Ion Plan, RST) and return a Plan object.
    """

    # if path is a directory, look for a RN*.dcm file
    if path.is_dir():
        plan_files = list(path.glob('RN*.dcm')) + list(path.glob('*.pld')) + list(path.glob('*.rst'))
        if not plan_files:
            raise FileNotFoundError(f"No plan files found in directory: {path}")
        if len(plan_files) > 1:
            logger.warning(f"Multiple plan files found in directory: {path}. Using the first one.")
        path = plan_files[0]

    suffix = path.suffix.lower()
    if suffix == '.pld':
        return load_plan_pld(path, **kwargs)
    elif suffix == '.dcm':
        return load_plan_dicom(path, **kwargs)
    elif suffix == '.rst':
        return load_plan_rst(path, **kwargs)
    else:
        raise ValueError(f"Unsupported plan file format: {suffix}")
