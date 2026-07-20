"""Logging setup shared by main.py and the rest of the package.

Three tiers, so you can pick the right amount of noise:

- default:    INFO   — milestones only (start/end of run, start/end of each
              domain, final summary). Good for "did it work."
- --verbose:  PROGRESS (15) — adds one line per resource fetched (path, item
              count, elapsed time) and per-page pagination progress. Good for
              "where did it slow down / what did it actually pull."
- --debug:    DEBUG (10) — adds raw HTTP request/response details, retry/
              backoff decisions, and pagination internals (offsets, whether a
              page was considered partial). Good for "why is this looping /
              why is this endpoint behaving oddly."

A dedicated PROGRESS level (rather than overloading INFO) means --verbose and
--debug are additive rather than the crawler needing to know which mode it's
in — code just calls the log method that matches how important the message is,
and the configured level filters it.
"""
from __future__ import annotations

import logging

PROGRESS = 15
logging.addLevelName(PROGRESS, "PROGRESS")


def _progress(self, message, *args, **kwargs):
    if self.isEnabledFor(PROGRESS):
        self._log(PROGRESS, message, args, **kwargs)


# Attach Logger.progress(...) the same way Logger.debug/info/warning work.
logging.Logger.progress = _progress  # type: ignore[attr-defined]


def setup_logging(verbose: bool = False, debug: bool = False) -> None:
    if debug:
        level = logging.DEBUG
    elif verbose:
        level = PROGRESS
    else:
        level = logging.INFO

    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        force=True,  # logging.basicConfig no-ops on repeat calls otherwise,
                     # which would silently ignore a later --debug/--verbose
                     # change if this is ever invoked more than once per process.
    )
    # requests/urllib3 are extremely chatty at DEBUG; keep them at INFO even
    # in --debug mode unless someone really wants HTTP library internals too.
    logging.getLogger("urllib3").setLevel(logging.INFO)
