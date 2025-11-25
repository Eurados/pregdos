import os
import subprocess
from pathlib import Path

OPENTOPAS_IMAGE = "opentopas-geant4"
UPLOAD_ROOT = Path("/tmp/pregdos_uploads")

def find_latest_study_dir() -> Path:
    if not UPLOAD_ROOT.exists():
        raise RuntimeError(f"{UPLOAD_ROOT} does not exist")
    dirs = [p for p in UPLOAD_ROOT.iterdir() if p.is_dir()]
    if not dirs:
        raise RuntimeError(f"No study dirs in {UPLOAD_ROOT}")
    return max(dirs, key=lambda p: p.stat().st_mtime)

def run_topas_in_docker(study_dir: Path) -> None:
    # expect topas_field*.txt directly inside study_dir
    field_files = sorted(study_dir.glob("topas_field*.txt"))
    if not field_files:
        raise RuntimeError(f"No topas_field*.txt in {study_dir}")

    # Shell-kommando inde i containeren:
    #  - sætter LD_LIBRARY_PATH så Geant4-biblioteker findes
    #  - cd'er til /work
    #  - kører topas med fuld sti for alle topas_field*.txt
    inner = (
        "export LD_LIBRARY_PATH=/opt/GEANT4/geant4-install/lib:$LD_LIBRARY_PATH && "
        "cd /work && "
        "for f in topas_field*.txt; do /opt/TOPAS/OpenTOPAS-install/bin/topas \"$f\"; done"
    )

    cmd = [
        "docker", "run", "--rm",
        "-v", f"{study_dir}:/work",
        "-v", f"{UPLOAD_ROOT}:/tmp/pregdos_uploads",
        OPENTOPAS_IMAGE,
        "bash", "-lc",
        inner,
    ]
    subprocess.check_call(cmd)

def main():
    study_dir = find_latest_study_dir()
    print(f"Using study dir: {study_dir}")
    run_topas_in_docker(study_dir)

if __name__ == "__main__":
    main()