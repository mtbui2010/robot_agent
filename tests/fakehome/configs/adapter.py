"""The fakebot RobotAdapter: the namemap shim + manifest, with the KAAIR extras the core asks
the robot for (what opens, which skills speak for themselves). Mirrors kcare_robot's
configs/adapter.py; change it only when the contract changes."""

from pathlib import Path

from robot_agent.contract import Manifest, NamemapAdapter

from . import grace_namemap


PHRASES = {
    "task_start": {"ko": "작업을 시작합니다"},
    "verb.MoveTo": {"ko": "이동"},
    "robot_description": {"en": "The robot is a mobile manipulator. It can:\n"
                                "move to a place, find an object, pick it up, place it on a surface, "
                                "open / close a drawer, put an object in an open drawer."},
    "verify_failed.Pick": {"en": "pick reported success but the gripper is empty"},
    "verify_failed.Place": {"en": "place reported success but the object is still in the gripper"},
    "verify_failed.PutIn": {"en": "place reported success but the object is still in the gripper"},
}


class Adapter(NamemapAdapter):
    quiet_skills = {"check_fall"}

    def __init__(self):
        super().__init__(grace_namemap, Manifest.from_yaml(Path(__file__).with_name("manifest.yaml")))
        self.i18n.phrases.update(PHRASES)

    @staticmethod
    def is_openable(key, entry):
        # the sim house ENV marks no drawer: its key names it
        return bool((entry or {}).get("openable")) or "drawer" in key.lower()
