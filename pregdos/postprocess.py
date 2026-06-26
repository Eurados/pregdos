"""Post-processing hooks called after SLURM job submission.

This module is the designated place for logic that should run once a job
folder is fully set up.  Currently all hooks are no-ops; they exist so that
the call sites in webserver.py are already in place when the results viewer
(CSV parsing, summary tables) is implemented.
"""


def post_process_job(job_dir: str) -> None:
    """Called after all TOPAS input files for a job have been submitted to SLURM.

    Parameters
    ----------
    job_dir:
        Absolute path to the job working directory (e.g.
        ``/home/slurm/jobs/study_20260626_143000``).  When the simulation
        completes, TOPAS writes scorer CSV files here alongside the SLURM
        output log.
    """
    # TODO: parse scorer CSV files and store a summary for the results viewer
    pass
