#!/usr/bin/env python3
"""Prepare a MeerK40t laser job from the kraft-house SVG.

This is a thin wrapper over the repo's material-profile-driven job preparation
library (``cli_anything.meerk40t.utils.job_prep``). All laser settings for
350gsm kraft card on a Sculpfun S9 come from the bundled ``kraft-350gsm``
material profile, which you can inspect with:

    cli-anything-meerk40t materials show kraft-350gsm --machine sculpfun-s9

The profile marks its cut and etch settings as estimated. This wrapper runs with
``allow_estimated=True`` and prints a scrap-first warning on stderr whenever
estimated roles are present. It never invents its own laser values.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Optional

from cli_anything.meerk40t.utils.job_prep import (
    _human_summary,
    prepare_job,
)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Prepare a MeerK40t laser job SVG and G-code from the kraft-house SVG."
    )
    parser.add_argument("input_svg", help="Input SVG following the layer colour contract")
    parser.add_argument("--out-dir", default=".", help="Directory for output files")
    parser.add_argument("--json", action="store_true", help="Emit JSON summary")
    args = parser.parse_args(argv)

    try:
        summary = prepare_job(
            args.input_svg,
            args.out_dir,
            machine="sculpfun-s9",
            material="kraft-350gsm",
            allow_estimated=True,
        )
    except Exception as exc:
        print(f"Job preparation failed: {exc}", file=sys.stderr)
        return 1

    estimated_roles = summary.get("estimated_roles") or []
    if estimated_roles:
        print(
            f"WARNING: estimated settings for {estimated_roles} - calibrate on scrap first",
            file=sys.stderr,
        )

    if not summary["verification"]["all_passed"]:
        print("G-code verification failed.", file=sys.stderr)
        if args.json:
            print(json.dumps(summary, indent=2))
        else:
            print(_human_summary(summary), file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(_human_summary(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
