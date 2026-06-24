"""ultracode fan-out hints (1.14.0): the derived `fanout_worthy` signal, the
`ultracode_signal` prompt token, the `ultracode_hints_enabled` config gate, and
the two emission surfaces — the HUMAN advisory (detail recap / SessionStart) in
default mode, and the MODEL steering on an ultracode turn (per-prompt hook).

Task Station never fires orchestration: it only hints, only on worthy tasks
(effort L/XL, or RESEARCH/REVIEW/DATA at M+), only for read/think phases,
and the steering keeps repo writes on the delegation path."""
import importlib.util
import io
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib")
sys.path.insert(0, LIB)

_spec = importlib.util.spec_from_file_location("task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)
import config


class _Args:
    def __init__(self, **kw):
        self.__dict__.update(kw)


# ------------------------------------------------------------- fanout_worthy ----
class FanoutWorthy(unittest.TestCase):
    """Pure, derived signal: TRUE on L/XL (any category) or a breadth category
    (orange=REVIEW · purple=RESEARCH · brown=DATA) at M+; FALSE for xs/s, an
    unset effort, and a plain question / untracked task."""

    def w(self, effort, color=None):
        return ts.fanout_worthy({"effort": effort, "color": color})

    def test_xs_s_never_worthy_any_category(self):
        for color in (None, "green", "orange", "purple", "brown", "red"):
            self.assertFalse(self.w("XS", color), "XS/%s" % color)
            self.assertFalse(self.w("S", color), "S/%s" % color)

    def test_unset_effort_never_worthy(self):
        self.assertFalse(self.w(None, "orange"))      # breadth category, no effort
        self.assertFalse(self.w(None, "green"))
        self.assertFalse(ts.fanout_worthy({"color": "purple"}))   # effort key absent
        self.assertFalse(ts.fanout_worthy({}))                    # bare task

    def test_open_question_no_effort_not_worthy(self):
        # A plain open (○) question / untracked task carries no effort estimate.
        self.assertFalse(ts.fanout_worthy({"status": "open", "color": "black"}))

    def test_L_XL_worthy_any_category(self):
        for color in (None, "green", "red", "blue", "orange", "purple", "brown"):
            self.assertTrue(self.w("L", color), "L/%s" % color)
            self.assertTrue(self.w("XL", color), "XL/%s" % color)

    def test_breadth_categories_worthy_at_M(self):
        for color in ("orange", "purple", "brown"):   # REVIEW / RESEARCH / DATA
            self.assertTrue(self.w("M", color), "M/%s" % color)

    def test_breadth_categories_not_worthy_below_M(self):
        for color in ("orange", "purple", "brown"):
            self.assertFalse(self.w("S", color), "S/%s" % color)

    def test_non_breadth_category_at_M_not_worthy(self):
        for color in ("green", "red", "blue", "black"):
            self.assertFalse(self.w("M", color), "M/%s" % color)

    def test_resolves_category_by_tag_or_emoji(self):
        # Category may be referenced by [TAG]/emoji, not just the slot key — the
        # helper resolves it (when the categories plugin is present).
        if ts.cats is None:
            self.skipTest("categories plugin not available")
        self.assertTrue(self.w("M", "RESEARCH"))   # tag → purple
        self.assertTrue(self.w("M", "🟣"))          # emoji → purple


# ------------------------------------------------------------ ultracode_signal --
class UltracodeSignal(unittest.TestCase):
    def test_keyword_present_true(self):
        self.assertTrue(ts.ultracode_signal("please ultracode this"))
        self.assertTrue(ts.ultracode_signal("Ultracode"))          # case-insensitive
        self.assertTrue(ts.ultracode_signal("let's ULTRACODE the audit"))

    def test_keyword_absent_false(self):
        self.assertFalse(ts.ultracode_signal("let's discuss the workflow"))
        self.assertFalse(ts.ultracode_signal(""))
        self.assertFalse(ts.ultracode_signal(None))
        # word-boundary: a substring inside another token does not count.
        self.assertFalse(ts.ultracode_signal("ultracoded"))
        self.assertFalse(ts.ultracode_signal("superultracode"))


# -------------------------------------------------------- ultracode_hints gate --
class HintsGate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["TASK_STATION_HOME"] = self.tmp
        os.environ.pop("TASK_STATION_ULTRACODE_HINTS", None)

    def tearDown(self):
        os.environ.pop("TASK_STATION_HOME", None)
        os.environ.pop("TASK_STATION_ULTRACODE_HINTS", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_default_on(self):
        self.assertTrue(config.ultracode_hints_enabled())

    def test_env_off_overrides(self):
        config.set("ultracode_hints", True)
        os.environ["TASK_STATION_ULTRACODE_HINTS"] = "off"
        self.assertFalse(config.ultracode_hints_enabled())

    def test_config_off_persists(self):
        config.set("ultracode_hints", False)
        self.assertFalse(config.ultracode_hints_enabled())
        import json
        with open(os.path.join(self.tmp, "config.json")) as f:
            self.assertFalse(json.load(f)["ultracode_hints"])

    def test_in_reset_keys(self):
        self.assertIn("ultracode_hints", config.RESET_KEYS)


# --------------------------------------------------------------- copy blocks ----
class CopyBlocks(unittest.TestCase):
    """Both blocks must carry the delegation-boundary wording so the read/think vs
    write distinction is never lost."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["TASK_STATION_HOME"] = self.tmp
        os.environ.pop("TASK_STATION_ULTRACODE_HINTS", None)

    def tearDown(self):
        os.environ.pop("TASK_STATION_HOME", None)
        os.environ.pop("TASK_STATION_ULTRACODE_HINTS", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_advisory_wording(self):
        adv = ts.ultracode_advisory({"effort": "L", "color": "green"})
        self.assertIn("ultracode", adv)
        self.assertIn("fan-out-worthy", adv)
        self.assertIn("delegation", adv)
        self.assertIn("never", adv.lower())

    def test_steering_wording(self):
        st = ts.ultracode_steering()
        self.assertIn("delegation", st)
        self.assertIn("never", st.lower())
        self.assertIn("MUTATION", st)


# --------------------------------------------------- human advisory: detail -----
class HumanAdvisoryDetail(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["TASK_STATION_HOME"] = self.tmp
        os.environ.pop("TASK_STATION_ULTRACODE_HINTS", None)
        ts.DATA = self.tmp
        ts.STORE = os.path.join(self.tmp, "store")
        ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
        ts.LINKS_DIR = os.path.join(ts.STORE, "links")

    def tearDown(self):
        os.environ.pop("TASK_STATION_HOME", None)
        os.environ.pop("TASK_STATION_ULTRACODE_HINTS", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed(self, **kw):
        t = ts.new_task("A sizeable task", "do the analysis", **kw)
        ts.save_task(t)
        ts.ensure_seqs()
        return ts.load_task(t["id"])

    _ADV = "ultracode: this task is fan-out-worthy"

    def test_present_for_worthy_task_hints_on(self):
        task = self._seed(effort="l", color="green")
        out = ts._format_detail(task, "sess")
        self.assertIn(self._ADV, out)

    def test_absent_for_non_worthy_task(self):
        task = self._seed(effort="s", color="green")
        out = ts._format_detail(task, "sess")
        self.assertNotIn(self._ADV, out)

    def test_absent_when_hints_off(self):
        config.set("ultracode_hints", False)
        task = self._seed(effort="xl", color="purple")
        out = ts._format_detail(task, "sess")
        self.assertNotIn(self._ADV, out)


# ------------------------------------------------ model steering: prompt hook ---
class ModelSteering(unittest.TestCase):
    """cmd_prompt_context prints the steering block ONLY when attached to a worthy
    task AND an ultracode signal is in the prompt AND hints are on — in addition to
    the normal activity-touch (existing behaviour is never suppressed)."""

    _STEER = "fan subagents out for read/analyze/design/review/verify ONLY"

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["TASK_STATION_HOME"] = self.tmp
        os.environ.pop("TASK_STATION_ULTRACODE_HINTS", None)
        ts.DATA = self.tmp
        ts.STORE = os.path.join(self.tmp, "store")
        ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
        ts.LINKS_DIR = os.path.join(ts.STORE, "links")

    def tearDown(self):
        os.environ.pop("TASK_STATION_HOME", None)
        os.environ.pop("TASK_STATION_PROMPT", None)
        os.environ.pop("TASK_STATION_ULTRACODE_HINTS", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _attach(self, session, **kw):
        t = ts.new_task("Big audit task", "review it", **kw)
        ts.save_task(t)
        ts.ensure_seqs()
        ts.set_link(session, t["id"])
        return ts.load_task(t["id"])

    def _run(self, session, prompt):
        os.environ["TASK_STATION_PROMPT"] = prompt
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_prompt_context(_Args(session=session))
        return buf.getvalue()

    def test_steering_on_worthy_task_with_signal(self):
        self._attach("s1", effort="l", color="green")
        out = self._run("s1", "let's ultracode the analysis")
        self.assertIn(self._STEER, out)

    def test_no_steering_without_signal(self):
        self._attach("s2", effort="l", color="green")
        out = self._run("s2", "keep working on the analysis")
        self.assertNotIn(self._STEER, out)

    def test_no_steering_for_non_worthy_task(self):
        self._attach("s3", effort="s", color="green")
        out = self._run("s3", "let's ultracode the analysis")
        self.assertNotIn(self._STEER, out)

    def test_no_steering_when_hints_off(self):
        config.set("ultracode_hints", False)
        self._attach("s4", effort="xl", color="purple")
        out = self._run("s4", "let's ultracode the analysis")
        self.assertNotIn(self._STEER, out)


if __name__ == "__main__":
    unittest.main()
