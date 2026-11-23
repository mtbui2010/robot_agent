"""S01 goldens, language (needed by S04, S08, S15, S18): a run in Korean (the translator prompts answered by the script), and
two destinations for one object (the validator's kind-less question). Recorded on the old code."""

from scenarios import CRATE, DS, DT, FLAT, GIZMO, flat, house, understood

LR = "sofa@living room"
# the made-up flat of the bt_goal cassettes with Korean aliases (선반 shelf, 벤치 bench, 상자 crate)
KFLAT = {k: dict(v, aliases=list(v["aliases"]) + [ko]) for (k, v), ko in zip(FLAT.items(), ("선반", "벤치", "상자"))}

SCENARIOS = [
    (dict(house("non_english", "사과를 책상에서 식탁으로 가져다 주고 소파로 가.", None,
                llm_script=[["You turn a household robot task into goals",
                             understood(("on", "apple", "desk", "dining table"), ("go", "", "", "sofa"))],
                            ["Give the usual Korean name", "apple: 사과"]],
                plan=["MoveTo " + DS, "Find apple", "Pick apple", "MoveTo " + DT, "Place " + DT, "MoveTo " + LR],
                options={"confirm_task": True, "input_pivot": None}, robot={"answers": ["네"]}, lang="ko"),
          llm={"script": [["You turn a household robot task into goals",
                           understood(("on", "apple", "desk", "dining table"), ("go", "", "", "sofa"))],
                          ["Give the usual Korean name", "apple: 사과"],
                          ["Translate into natural.*Is that right\\?", "책상에 있는 사과를 식탁에 놓고 소파로 갈게요. 맞나요?"]],
               "default": "네, 알겠어요."}),
     ["question", "success", "non_english"], "a run in Korean: the read-back of an on and a go goal, the step lines and the "
     "end said in Korean (the language pack's phrases, the translator's prompts answered by the script)"),
    (dict(house("two_destinations", "Put the apple on the dining table and on the counter.", None,
                robot={"answers": ["the dining table"]}),
          llm={"script": [understood(("on", "apple", "desk", "dining table"), ("on", "apple", "desk", "counter"))],
               "default": understood(("on", "apple", "desk", "dining table"))}),
     ["question", "failure"], "two destinations for one object: the validator's question (no kind) answered; the read-back's "
     "issue (the counter was said) asked and not answered: the run stops"),
    (dict(flat("non_english_menu", objects={"gizmo": CRATE}, search_legs=0, answers=["그만"]), env=KFLAT,
          lang="ko", task="선반에 있는 기즈모를 벤치에 놓아 줘.",
          llm={"script": [["You turn a household robot task into goals",
                           {"status": "clear", "reason": "", "question": "", "goals": [dict(GIZMO)]}],
                          ["Give the usual Korean name", "gizmo: 기즈모"]],
               "default": "네, 알겠어요."}),
     ["bt_goal", "question", "stop", "non_english"], "the bt_goal menu question said from the Korean language pack's phrase, "
     "answered with a Korean stop word"),
]
