import logging
from pathlib import Path

from pregdos.model_ct import CTModel

logger = logging.getLogger(__name__)


def load_ct(path: Path) -> CTModel:
    ct_model = CTModel()
    # TODO: Implement loading of CT data from the specified path
    return ct_model
