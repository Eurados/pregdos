#!/usr/bin/env python3
"""Prove an installed PregDos actually works, from inside the environment it was installed in.

    $ /opt/pregdos/venv/bin/python packaging/verify_offline_install.py

Run against the venv built from the wheelhouse, on a machine with no network.  This is the
step that makes the artifact trustworthy: the alternative is discovering the gap at the
airgapped machine, where it cannot be investigated.

**It renders pages, it does not merely import.**  Everything under ``templates/``,
``static/``, ``data/spr_tables/`` and ``data/beam_models/`` is resolved at runtime through
``importlib.resources.files("pregdos")``, so a package-data glob that failed to ship is a
broken page at the hospital, not an ImportError in CI.  Only a request catches it.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if condition else 'FAIL'}  {label}{f' -- {detail}' if detail else ''}")
    if not condition:
        FAILURES.append(label)


def main() -> int:
    # An airgapped site's config, exercised for real: this both switches off the update check
    # (which would otherwise wait on a DNS lookup that cannot succeed) and proves the config
    # file is readable from an installed package rather than only from a source checkout.
    work = Path(tempfile.mkdtemp(prefix="pregdos-verify-"))
    config_path = work / "config.toml"
    config_path.write_text(
        f'[paths]\nwork_dir = "{work / "studies"}"\n\n[network]\nupdate_check = false\n',
        encoding="utf-8",
    )
    import os
    os.environ["PREGDOS_CONFIG"] = str(config_path)

    print("Imports and package data:")
    import importlib.metadata

    from pregdos import config, report_pdf, topas_scorer, webserver

    check("pregdos imports", True, importlib.metadata.version("pregdos"))
    check("dicomexport is installed", bool(importlib.metadata.version("dicomexport")),
          importlib.metadata.version("dicomexport"))
    check("config.toml.example ships", "[scheduler]" in config.example_text())
    check("config file is honoured", config.load().network.update_check is False)

    tables = webserver._builtin_spr_tables()
    check("data/spr_tables/ ships", len(tables) > 0, f"{len(tables)} tables")
    models = webserver._builtin_beam_models()
    check("data/beam_models/ ships", len(models) > 0, f"{len(models)} models")
    check("data/*.csv ships", len(topas_scorer.SCORER_DEFS) > 0)

    # static/img needs its own package-data glob, because "static/*" does not descend.
    logos = webserver._funding_logos()
    check("static/img/ ships", len(logos) == len(webserver._FUNDING_LOGOS),
          f"{len(logos)}/{len(webserver._FUNDING_LOGOS)} funding logos resolve")
    check("static/ ships", (report_pdf.importlib.resources.files("pregdos")
                            / "static" / "styles.css").is_file())

    print("\nRendered pages (templates/ and static/):")
    webserver.app.config["TESTING"] = True
    webserver._apply_config()
    with webserver.app.test_client() as client:
        for route in ("/", "/studies", "/about"):
            response = client.get(route)
            body = response.get_data(as_text=True)
            check(f"GET {route}", response.status_code == 200, f"HTTP {response.status_code}")
            check(f"{route} rendered a real page", "<html" in body.lower() and len(body) > 500,
                  f"{len(body)} bytes")

        # The About page names the bundled versions, so an empty one means the template
        # rendered but its data did not resolve.
        about = client.get("/about").get_data(as_text=True)
        check("/about reports a dicomexport version", "dicomexport" in about)
        check("/about shows the update check is off", "update check disabled" in about)

    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} check(s): {', '.join(FAILURES)}")
        return 1
    print("All checks passed -- this install is complete and serves pages offline.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
