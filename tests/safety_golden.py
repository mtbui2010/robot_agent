"""Corpus and table builder of the golden safety decisions (``tests/golden/safety_household_r17.json``).

The JSON was generated ONCE from the r17 code (hazard tables in the core), before the per-robot profile existed. The same
table must come out of the engine with the household fixture profile. ``table(decide)`` takes any function
``decide(goal, places, pack_rules, mode, receivers) -> Optional[str]`` and returns ``{n, sha256, hits}``: the digest covers
EVERY decision of the corpus in order (None included), ``hits`` lists the refused ones.
"""
import hashlib
import itertools
import json


class Places:
    def __init__(self, env):
        self.env = env


OBJECTS = [
    # cleaning products (EN / KO), sourced and assumed
    "bleach", "bleach bottle", "Chlorine bleach", "ammonia cleaner", "glass cleaner", "drain opener", "toilet bowl cleaner",
    "oven cleaner", "mildew remover", "detergent", "dish soap", "laundry pod", "dishwasher pod", "disinfectant",
    "rust remover", "insecticide", "lye", "cleaning spray", "floor cleaner",
    "표백제", "락스", "세제", "세정제", "배수구 클리너", "곰팡이 제거제", "살충제", "소독제", "변기 세정제", "식기세척기 세제",
    # safe things
    "milk", "orange juice", "glasses", "mug", "apple", "water bottle", "vacuum cleaner", "book", "remote", "plate", "knife",
    "우유", "컵", "사과", "리모컨", "책", "접시", "칼", "xyzzy", "",
]
SAID = ["", "the bleach", "락스 좀", "some soap", "juice"]
PLACES = {
    "fridge@kitchen": ["refrigerator"], "freezer@kitchen": [], "pantry@kitchen": ["larder"], "kimchi fridge@veranda": ["김치냉장고"],
    "bread box@kitchen": [], "냉장고@주방": [], "냉동실@주방": [],
    "stove@kitchen": ["hob", "cooktop"], "oven@kitchen": [], "microwave@kitchen": ["전자레인지"], "cutting board@kitchen": ["도마"],
    "rice cooker@kitchen": ["밥솥"], "가스레인지@주방": [],
    "dining table@kitchen": [], "table@kitchen": [], "counter@kitchen": ["kitchen counter"], "sink cabinet@kitchen": ["sink cabinet"],
    "shelf@living room": [], "sofa@living": ["couch"], "kids room": [], "cupboard@hall": [], "bookshelf@living": [], "tv stand@living": [],
    "shoe cabinet@living": [], "책장@거실": [], "소파@거실": [],
}
CATEGORIES = {"cat_fridge@x": "fridge", "cat_pantry@x": "pantry", "cat_stove@x": "stove", "cat_none@x": "shelf"}
PERSONS = {
    "child": {"person": "child", "aliases": ["the kid", "아이", "my daughter"]},
    "kid": {"person": "kid", "aliases": []},
    "dad": {"person": "dad", "aliases": ["my father"]},
    "mom": {"person": "mom", "aliases": ["엄마"]},
    "아이": {"person": "아이", "aliases": []},
    "toddler_x": {"person": "toddler_x", "aliases": ["blik"], "age_group": "toddler"},
    "x1": {"person": "x1", "aliases": ["아이"]},
    "x2": {"person": "x2", "aliases": ["blik"], "age_group": "adult"},
    "grandpa": {"person": "grandpa", "aliases": [], "category": "elderly"},
    "baby": {"person": "baby", "aliases": ["infant"]},
    "zorp": {"person": "zorp", "aliases": []},
}
PACK = [
    {"objects": ["knife"], "places": ["stove"], "say": "legacy knife rule"},
    {"objects": ["bleach"], "places": ["food_storage"], "say": "legacy food rule"},
    {"objects": ["milk"], "places": ["kids room"]},
]
MODES = ["core", "pack", "off"]


def env():
    e = {k: {"aliases": list(a), "loc": {"x": 1, "y": 1}} for k, a in PLACES.items()}
    e.update({k: {"aliases": [], "category": c, "loc": {"x": 1, "y": 1}} for k, c in CATEGORIES.items()})
    e.update({k: {**v, "loc": {"x": 1, "y": 1}} for k, v in PERSONS.items()})
    return e


def goals():
    e = env()
    objs = OBJECTS
    tos = list(e) + ["nowhere", ""]
    out = []
    for o in objs:                                   # every object into every place (type in / on, said empty)
        for to in tos:
            out.append({"type": "in" if "@" in to else "on", "object": o, "from": "", "to": to})
    for said in SAID:                                # said text alone, with a few objects
        for o in ("", "mug", "detergent"):
            for to in ("fridge@kitchen", "stove@kitchen", "child", "table@kitchen"):
                out.append({"type": "in", "object": o, "said": said, "from": "", "to": to})
    for o in ("bleach bottle", "glasses", "락스", "detergent"):    # near_person
        for to in ("table@kitchen", "fridge@kitchen", "shelf@living room"):
            for np in list(PERSONS) + ["", "kids room"]:
                out.append({"type": "on", "object": o, "from": "", "to": to, "near_person": np})
    for t in ("at", "holding", "opened", "unmoved"):  # kinds that are never checked
        out.append({"type": t, "object": "bleach", "to": "fridge@kitchen"})
    return out


def table(decide):
    places = Places(env())
    h, hits, n = hashlib.sha256(), [], 0
    gs = goals()
    for g, (mode, pack, rec) in itertools.product(gs, [(m, p, r) for m in MODES for p in (False, True) for r in (False, True)]):
        res = decide(g, places, PACK if pack else (), mode, rec)
        h.update(json.dumps([res], ensure_ascii=False).encode())
        n += 1
        if res:
            hits.append([g.get("type"), g.get("object"), g.get("said", ""), g.get("to"), g.get("near_person", ""), mode, int(pack), int(rec), res])
    return {"n": n, "sha256": h.hexdigest(), "hits": hits}
