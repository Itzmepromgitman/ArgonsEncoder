import os

import pytest

from ui_snapshot_utils import SURFACES, build_surface, dump_golden, load_golden


def _payload(name):
    text, rows = build_surface(name)
    return {"surface": name, "text": text, "rows": rows}


@pytest.mark.parametrize("name", SURFACES)
def test_surface_matches_golden(name):
    payload = _payload(name)
    if os.environ.get("UPDATE_SNAPSHOTS"):
        dump_golden(name, payload)
        return
    assert payload == load_golden(name), (
        f"{name} UI drifted from its snapshot. Review the change, then "
        f"re-run with UPDATE_SNAPSHOTS=1 to accept it."
    )
