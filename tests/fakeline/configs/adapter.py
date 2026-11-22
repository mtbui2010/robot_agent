"""The fakeline RobotAdapter: a made-up assembly-line cart (NOT a household robot) for the golden traces of the core
generalization (docs/proposals/generalize_core_design.md, Step S01). It drives between stations, picks and places parts,
says sentences, waits for a person to unload its tray, and has two actions no household robot has (``Inspect``,
``Fasten``). Schema 1 (today's manifest format, no schema field): the old code loads it unchanged. Facts here are made up
(``assumed``); change them only when the contract changes."""

from pathlib import Path

from robot_agent.contract import Manifest, NamemapAdapter, Unbindable

from . import sage_namemap

PHRASES = {
    "robot_description": {"en": "The robot is an assembly-line cart with one arm. It can:\n"
                                "drive to a station, find a part, pick it up, place it on a station, inspect a part, "
                                "fasten a part, say a sentence, wait until a person unloads its tray."},
    "delivery": {"en": "Please take the part from my tray."},
}
NO_OBJECT = {"WaitForUnload"}            # actions whose step has no object (goals.compile_steps writes "")


class Adapter(NamemapAdapter):

    def __init__(self):
        super().__init__(sage_namemap, Manifest.from_yaml(Path(__file__).with_name("manifest.yaml")))
        self.i18n.phrases.update(PHRASES)

    def bind(self, step, world=None):
        action = step.get("action") if isinstance(step, dict) else getattr(step, "action", None)
        obj = step.get("object") if isinstance(step, dict) else getattr(step, "object", None)
        if action in NO_OBJECT and not obj:
            spec = self.manifest.skill_for(action)
            if spec is None:
                raise Unbindable(f"no skill for {action}")
            return spec.name, {}
        return super().bind(step, world)

    @staticmethod
    def is_openable(key, entry):
        return False
