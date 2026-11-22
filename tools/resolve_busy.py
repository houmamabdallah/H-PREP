"""Guard project switches while a saved project is open in Resolve.
Pass wanted when the caller can reuse the open project. An operator can
authorize a switch for one run with RESOLVE_MAY_SWITCH=1."""
import os
import sys
from pathlib import Path


class HeIsWorking(RuntimeError):
    """Resolve has a project open, so this run would close it."""


def open_project(resolve):
    """The name of the project Resolve has open, or None. Never raises."""
    try:
        p = resolve.GetProjectManager().GetCurrentProject()
        return p.GetName() if p else None
    except Exception:
        return None


def refuse_if_he_is_working(resolve, what="this run", wanted=None):
    """Raise HeIsWorking if a project is open. Returns the current page when it is safe to carry on.

    `wanted` is the project this run would load: when it is already the open one, no switch happens and
    the caller can continue using the existing session.
    """
    if os.environ.get("RESOLVE_MAY_SWITCH") == "1":
        return None
    name = open_project(resolve)
    if wanted and name == wanted:
        try:
            return resolve.GetCurrentPage()
        except Exception:
            return None
    if not name:
        try:
            return resolve.GetCurrentPage()
        except Exception:
            return None
    page = ""
    try:
        page = resolve.GetCurrentPage() or ""
    except Exception:
        pass
    raise HeIsWorking(
        "%s would close the open project: %s%s. Loading a project in Resolve closes the current one, "
        "so this run stops here. Close the project first, or set RESOLVE_MAY_SWITCH=1 for an intentional switch." % (what, name, " (on the %s page)" % page if page else "")
    )


def main(argv=None):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "prep"))
    from hprep_config import setting_path
    sys.path.append(str(setting_path("resolve_modules")))
    import DaVinciResolveScript as dvr
    r = dvr.scriptapp("Resolve")
    if r is None:
        print("Resolve is not running")
        return 0
    name = open_project(r)
    print("open project: %s" % (name or "(none)"))
    try:
        refuse_if_he_is_working(r, "a project switch")
    except HeIsWorking as e:
        print("REFUSE: %s" % e)
        return 1
    print("safe: no project is open")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
