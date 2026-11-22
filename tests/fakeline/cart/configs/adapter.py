"""``fakeline.cart``: the armless tray cart of the made-up assembly line (tests/fakeline): it drives, says sentences and waits
for a person to unload its tray. No arm: a goal that moves an object is refused with the actions it lacks. Made up (assumed)."""

from pathlib import Path

from robot_agent.contract import Manifest, NamemapAdapter


from . import sage_namemap


class Adapter(NamemapAdapter):

    def __init__(self):
        super().__init__(sage_namemap, Manifest.from_yaml(Path(__file__).with_name("manifest.yaml")))
        self.i18n.phrases.update({"cannot_do": {"en": "I have no arm."}})

    def bind(self, step, world=None):
        action = step.get("action") if isinstance(step, dict) else getattr(step, "action", None)
        obj = step.get("object") if isinstance(step, dict) else getattr(step, "object", None)
        if action == "WaitForUnload" and not obj:
            return "wait_for_unload", {}
        return super().bind(step, world)

    @staticmethod
    def is_openable(key, entry):
        return bool((entry or {}).get("openable"))            # a tote with a lid
