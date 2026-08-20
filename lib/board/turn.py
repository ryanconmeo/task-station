# turn.py
"""THE DRIVEN TURN — the surface, ahead of the behaviour.

Tests first: this file exists so `tests/test_turn.py` imports and FAILS ON ITS
ASSERTIONS rather than on a missing module. Every body here is deliberately empty of
judgement — the behaviour lands in the next commit.
"""

UNSTARTED = "unstarted"
MANUAL = "manual-pending"
SPAWN_FAILED = "spawn-failed"
RUNNING = "running"
REPORTED = "reported"
SILENT_EXIT = "silent-exit"
PARKED = "parked"
SETTLED = "settled"
CHILD_STATES = (UNSTARTED, MANUAL, SPAWN_FAILED, RUNNING, REPORTED, SILENT_EXIT,
                PARKED, SETTLED)

INVOKE = "invoke"
RELAUNCH = "relaunch"
WAIT = "wait"
GATE = "gate"
GRADE = "grade"
RETRY = "retry"
PARK = "park"
RELEASE = "release"
ACTIONS = (INVOKE, RELAUNCH, WAIT, GATE, GRADE, RETRY, PARK, RELEASE)

HALT_COMPLETE = "complete"
HALT_PARKED = "parked"
HALT_WORKING = "working"
HALT_EMPTY = "empty"
HALT_BUDGET = "budget"


def launches(task):
    return []


def last_launch(task):
    return None


def report_memo(task, after=None):
    return None


def unacked(task):
    return []


def worked_since(task, ts):
    return False


def child_state(task, live=(), worked=None):
    return UNSTARTED


def ran_count(output):
    return None


def suite_green(output, minimum=1):
    return False, ""


def landed(diff_output):
    return False


def landed_probe(branch, merge="origin/main"):
    return ""


def condition_lint(cmd, expect):
    return []


def shell_syntax_error(cmd):
    return None


def number_without_command(steps):
    return []


def stale_install(repo_version, installed_version):
    return None


def gate(task, live=(), worked=None, landed=None, installed=None, version=None):
    return {"seq": task.get("seq"), "state": UNSTARTED, "findings": [],
            "clean": True, "gradeable": True}


def rejection_memo(v, ref=None, note=None, findings=None):
    return ""


def retry_decision(task, v, retry_max=None, park=None):
    return {"do": WAIT, "reason": None, "left": 0}


def plan(orch, children, live=(), resolve=None, cap=None, retry_max=None, worked=None,
         ask=None):
    return {"scan": {"stop": None, "totals": {}, "rows": []}, "actions": [],
            "halt": None, "budget": {"max": 0, "running": [], "over": False},
            "states": {}}


def lines(p):
    return []
