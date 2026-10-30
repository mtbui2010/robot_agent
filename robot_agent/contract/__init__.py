"""The contract between the robot-agnostic core and a robot package.

Pure stdlib (+ pyyaml for manifest files): importing it boots nothing.

    from robot_agent.contract import FailureCode, SkillResult, Manifest, load_adapter

See docs/contract.md.
"""

from .adapter import RobotAdapter, Unbindable
from .failures import SOFTWARE_ERR, classify_legacy_failure, is_software_error
from .legacy_adapter import NamemapAdapter, load_adapter
from .manifest import Manifest, ManifestError, load_manifest
from .recovery import DEFAULT_RECOVERY, Recovery, RecoveryPolicy, RecoveryRule, policy_for
from .types import (FailureCode, Geometry, Messages, Observation, SkillResult, SkillSpec, VerifySpec,
                    VERIFY_KINDS)

__all__ = [
    "FailureCode", "SkillResult", "VerifySpec", "SkillSpec", "Manifest", "ManifestError", "load_manifest",
    "RobotAdapter", "Unbindable", "Observation", "Geometry", "Messages", "VERIFY_KINDS",
    "classify_legacy_failure", "is_software_error", "SOFTWARE_ERR",
    "Recovery", "RecoveryRule", "RecoveryPolicy", "DEFAULT_RECOVERY", "policy_for",
    "NamemapAdapter", "load_adapter",
]
