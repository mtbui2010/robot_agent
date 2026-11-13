"""Shortcut-button groups: a button's group, the ordered groups file, deleting a group (buttons go to Other), the API."""

import json

import pytest

from robot_agent.core.button_manager import ButtonManager


@pytest.fixture
def bm(tmp_path):
    return ButtonManager(tmp_path)


def test_buttons_without_a_group_are_in_other(bm, tmp_path):
    (tmp_path / "buttons.json").write_text(json.dumps([{"id": "a", "label": "fold", "plan": "movej::fold"}]))
    bm.load_saved()
    assert bm.all() == [{"id": "a", "label": "fold", "plan": "movej::fold", "group": ""}]


def test_groups_are_ordered_and_named_uniquely(bm):
    head = bm.add_group("Head")
    arm = bm.add_group("Arm")
    assert [g["name"] for g in bm.groups()] == ["Head", "Arm"]
    for bad in ("", "  ", "head", "Other", "other"):
        with pytest.raises(ValueError):
            bm.add_group(bad)
    assert bm.reorder_groups([arm["id"], head["id"]]) is True
    assert [g["name"] for g in bm.groups()] == ["Arm", "Head"]
    assert bm.reorder_groups([arm["id"]]) is False                    # not the same set
    assert bm.rename_group(head["id"], "Neck")["name"] == "Neck"
    with pytest.raises(ValueError):
        bm.rename_group(head["id"], "Arm")                            # taken
    assert bm.rename_group("nope", "X") is None


def test_a_button_joins_moves_and_leaves_a_group(bm):
    g = bm.add_group("Head")
    b = bm.add("head left", "moveh::left", group=g["id"])
    assert b["group"] == g["id"]
    assert bm.update(b["id"], group="")["group"] == ""                # to Other
    assert bm.update(b["id"], group=g["id"])["group"] == g["id"]
    assert bm.update(b["id"], label="left")["group"] == g["id"]       # None = unchanged
    with pytest.raises(ValueError):
        bm.add("x", "y", group="missing")
    with pytest.raises(ValueError):
        bm.update(b["id"], group="missing")


def test_deleting_a_group_moves_its_buttons_to_other(bm):
    g, h = bm.add_group("Head"), bm.add_group("Arm")
    a = bm.add("head left", "moveh::left", group=g["id"])
    b = bm.add("fold", "movej::fold", group=h["id"])
    assert bm.remove_group(g["id"]) is True
    by = {x["id"]: x for x in bm.all()}
    assert by[a["id"]]["group"] == "" and by[b["id"]]["group"] == h["id"]   # kept; the other group untouched
    assert len(by) == 2
    assert bm.remove_group(g["id"]) is False
    assert [x["name"] for x in bm.groups()] == ["Arm"]


def test_an_unknown_group_reads_as_other_and_survives_a_restart(bm, tmp_path):
    g = bm.add_group("Head")
    b = bm.add("head left", "moveh::left", group=g["id"])
    bm2 = ButtonManager(tmp_path)
    bm2.load_saved()
    assert bm2.all()[0]["group"] == g["id"] and [x["name"] for x in bm2.groups()] == ["Head"]
    (tmp_path / "button_groups.json").write_text("[]")                # another machine deleted the group
    bm3 = ButtonManager(tmp_path)
    bm3.load_saved()
    assert bm3.all()[0]["id"] == b["id"] and bm3.all()[0]["group"] == ""


def test_bulk_import_keeps_a_known_group_only(bm):
    g = bm.add_group("Head")
    out = bm.bulk_import([{"label": "a", "plan": "p", "group": g["id"]}, {"label": "b", "plan": "p", "group": "gone"}])
    assert [x["group"] for x in out] == [g["id"], ""]


def test_api(monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from robot_agent.api import buttons as api
    class _S:
        bm = ButtonManager(tmp_path)
    monkeypatch.setattr(api, "current", lambda: _S())
    app = FastAPI(); app.include_router(api.router)
    c = TestClient(app)
    g = c.post("/buttons/groups", json={"name": "Head"}).json()
    assert c.post("/buttons/groups", json={"name": "head"}).status_code == 400
    b = c.post("/buttons", json={"label": "left", "plan": "moveh::left", "group": g["id"]}).json()
    assert b["group"] == g["id"]
    assert c.post("/buttons", json={"label": "x", "group": "missing"}).status_code == 400
    assert c.put(f"/buttons/{b['id']}", json={"group": ""}).json()["group"] == ""
    assert c.get("/buttons/groups").json() == [g]
    assert c.put(f"/buttons/groups/{g['id']}", json={"name": "Neck"}).json()["name"] == "Neck"
    assert c.put("/buttons/groups/zzz", json={"name": "A"}).status_code == 404
    assert c.post("/buttons/groups/reorder", json={"ids": [g["id"]]}).status_code == 200
    assert c.post("/buttons/groups/reorder", json={"ids": []}).status_code == 400
    c.put(f"/buttons/{b['id']}", json={"group": g["id"]})
    assert c.delete(f"/buttons/groups/{g['id']}").status_code == 200
    assert c.get("/buttons").json()[0]["group"] == ""                 # the button stayed, in Other
    assert c.delete(f"/buttons/groups/{g['id']}").status_code == 404
