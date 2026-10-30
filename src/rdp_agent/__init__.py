"""RDP Agent: runs scripted steps on a remote Windows desktop over RDP and reports with screenshots."""

from .actions import ACTIONS, StepError
from .config import Config
from .runner import Runner, RunResult, Script

__all__ = ["ACTIONS", "Config", "RunResult", "Runner", "Script", "StepError"]
__version__ = "0.1.0"
