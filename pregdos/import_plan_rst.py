import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def load_plan_rst(file_rst: Path, scaling=1.0):
    """this is implemented in pytrip, maybe we could import it?."""
    logger.error("RST reader not implemented yet.")
    raise NotImplementedError("RST import is not implemented yet.")
