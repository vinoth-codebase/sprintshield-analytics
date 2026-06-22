"""SprintShield Analytics - Unplanned Support Demand & Developer Capacity Forecaster.

A capacity governance engine that quantifies sprint engineering capacity lost to
unplanned production support across multi-tower data organizations, and forecasts
delivery confidence with stochastic demand modelling and Monte Carlo simulation.
"""

from __future__ import annotations

import os

__version__ = "1.0.0"
__author__ = "Vinothkumar N"
__license__ = "MIT"

__all__ = ["__version__", "__author__", "__license__"]


def _relax_address_space_limit() -> bool:
    """Lift a low soft address-space limit up to the hard limit, if permitted.

    Why this exists
    ---------------
    The Delta Lake adapter runs on the Rust-backed ``deltalake`` engine, which
    spawns a worker pool per table operation. Some container runtimes (notably
    memory-capped CI pods) ship a *soft* ``RLIMIT_AS`` well below the hard
    limit. Because the scientific stack already reserves a large amount of
    address space, a thread spawn can then fail inside the native allocator and
    ``abort()`` the whole interpreter -- a failure that cannot be caught from
    Python.

    Raising the soft limit to the hard limit is safe: it only ever *permits*
    more virtual address space, never reserves it. On an ordinary workstation
    the soft and hard limits are both unlimited and this function is a no-op.

    Set ``SPRINTSHIELD_RELAX_RLIMIT=0`` to disable the behaviour entirely.
    """
    if os.environ.get("SPRINTSHIELD_RELAX_RLIMIT", "1").lower() in {"0", "false", "no"}:
        return False
    try:
        import resource

        soft, hard = resource.getrlimit(resource.RLIMIT_AS)
        if hard == resource.RLIM_INFINITY and soft != resource.RLIM_INFINITY:
            resource.setrlimit(resource.RLIMIT_AS, (resource.RLIM_INFINITY, resource.RLIM_INFINITY))
            return True
    except (ImportError, ValueError, OSError, AttributeError):  # pragma: no cover
        # Non-POSIX platform, or the process is not allowed to change the limit.
        pass
    return False


_RELAXED_ADDRESS_SPACE = _relax_address_space_limit()
