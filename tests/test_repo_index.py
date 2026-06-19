import os, sys, json, tempfile, shutil, unittest
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
import repo_index


def _mkrepo(root, name, manifests=(), git=True):
    """Create a fake repo dir under root with an optional .git marker and
    manifest files. Returns the repo path."""
    path = os.path.join(root, name)
    os.makedirs(path, exist_ok=True)
    if git:
        os.mkdir(os.path.join(path, ".git"))
    for m in manifests:
        open(os.path.join(path, m), "w").close()
    return path


class Discovery(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_finds_only_git_children(self):
        _mkrepo(self.tmp, "alpha")
        _mkrepo(self.tmp, "beta")
        os.makedirs(os.path.join(self.tmp, "not-a-repo"))      # no .git → ignored
        open(os.path.join(self.tmp, "loose.txt"), "w").close()  # a file → ignored
        repos = repo_index.discover([self.tmp])
        names = sorted(r["name"] for r in repos)
        self.assertEqual(names, ["alpha", "beta"])
        for r in repos:
            self.assertTrue(os.path.isabs(r["path"]))

    def test_missing_root_is_tolerated(self):
        repos = repo_index.discover([os.path.join(self.tmp, "nope")])
        self.assertEqual(repos, [])


class AdoProject(unittest.TestCase):
    def test_ado_remote(self):
        self.assertEqual(
            repo_index.parse_ado_project(
                "https://IWGDevops@dev.azure.com/IWGDevops/Volt/_git/volt-api"),
            "Volt")

    def test_github_remote(self):
        self.assertEqual(
            repo_index.parse_ado_project("git@github.com:ryanconmeo/task-station.git"),
            "ryanconmeo/task-station")
        self.assertEqual(
            repo_index.parse_ado_project("https://github.com/ryanconmeo/task-station"),
            "ryanconmeo/task-station")

    def test_no_remote(self):
        self.assertIsNone(repo_index.parse_ado_project(None))
        self.assertIsNone(repo_index.parse_ado_project(""))
        self.assertIsNone(repo_index.parse_ado_project("/some/local/path.git"))


class Stack(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _stack(self, *manifests):
        p = _mkrepo(self.tmp, "r", manifests=manifests)
        try:
            return repo_index._detect_stack(p)
        finally:
            shutil.rmtree(p)

    def test_per_manifest(self):
        self.assertEqual(self._stack("App.csproj"), ["dotnet"])
        self.assertEqual(self._stack("Sln.sln"), ["dotnet"])
        self.assertEqual(self._stack("package.json"), ["node"])
        self.assertEqual(self._stack("pyproject.toml"), ["python"])
        self.assertEqual(self._stack("setup.py"), ["python"])
        self.assertEqual(self._stack("go.mod"), ["go"])
        self.assertEqual(self._stack("Cargo.toml"), ["rust"])
        self.assertEqual(self._stack("pom.xml"), ["jvm"])
        self.assertEqual(self._stack("build.gradle"), ["jvm"])
        self.assertEqual(self._stack(), [])

    def test_multiple_stacks(self):
        self.assertEqual(sorted(self._stack("package.json", "go.mod")), ["go", "node"])


class Status(unittest.TestCase):
    def test_active_vs_stale_vs_unknown(self):
        now = 1_000_000_000
        recent = now - 10 * 86400            # 10 days ago → active
        old = now - 400 * 86400              # >6 months ago → stale
        self.assertEqual(repo_index._status_from_ct(recent, now=now), "active")
        self.assertEqual(repo_index._status_from_ct(old, now=now), "stale")
        self.assertEqual(repo_index._status_from_ct(None, now=now), "unknown")


class Overrides(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_merge_overrides_win(self):
        repos = [{"name": "volt-api", "status": "active", "stack": ["dotnet"]}]
        ov = {"volt-api": {"summary": "Volt billing API",
                           "keywords": ["billing", "invoices"],
                           "domain": ["finance"],
                           "status": "stale"}}
        merged = repo_index.merge_overrides(repos, ov)
        r = merged[0]
        self.assertEqual(r["summary"], "Volt billing API")
        self.assertEqual(r["keywords"], ["billing", "invoices"])
        self.assertEqual(r["domain"], ["finance"])
        self.assertEqual(r["status"], "stale")   # override wins

    def test_missing_overrides_defaults(self):
        repos = [{"name": "x", "status": "active"}]
        merged = repo_index.merge_overrides(repos, {})
        self.assertEqual(merged[0]["summary"], "")
        self.assertEqual(merged[0]["keywords"], [])
        self.assertEqual(merged[0]["domain"], [])

    def test_load_overrides_missing_file_ok(self):
        self.assertEqual(repo_index._load_overrides(self.tmp), {})


class Match(unittest.TestCase):
    def test_obvious_term_ranks_first(self):
        repos = [
            {"name": "task-station", "keywords": ["tasks"], "domain": [], "stack": ["python"], "path": "/w/task-station"},
            {"name": "volt-billing", "keywords": ["invoices", "billing"], "domain": ["finance"], "stack": ["dotnet"], "path": "/w/volt-billing"},
            {"name": "marketing-site", "keywords": [], "domain": [], "stack": ["node"], "path": "/w/marketing-site"},
        ]
        ranked = repo_index.match("billing invoice work", repos)
        self.assertEqual(ranked[0]["name"], "volt-billing")

    def test_name_match_ranks_first(self):
        repos = [
            {"name": "alpha", "keywords": [], "domain": [], "stack": [], "path": "/w/alpha"},
            {"name": "beta", "keywords": [], "domain": [], "stack": [], "path": "/w/beta"},
        ]
        self.assertEqual(repo_index.match("beta", repos)[0]["name"], "beta")


class BuildIndex(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = os.path.join(self.tmp, "ws"); os.makedirs(self.root)
        self.data = os.path.join(self.tmp, "data"); os.makedirs(self.data)
    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_writes_md_and_json(self):
        _mkrepo(self.root, "alpha", manifests=["package.json"])
        _mkrepo(self.root, "beta", manifests=["go.mod"])
        # overrides survive regeneration and win
        with open(os.path.join(self.data, "repos.overrides.json"), "w") as f:
            json.dump({"alpha": {"summary": "the alpha service", "keywords": ["edge"]}}, f)

        repos = repo_index.build_index([self.root], data_dir=self.data)
        names = sorted(r["name"] for r in repos)
        self.assertEqual(names, ["alpha", "beta"])

        with open(os.path.join(self.data, "repos.md")) as f:
            md = f.read()
        self.assertIn("alpha", md)
        self.assertIn("the alpha service", md)
        self.assertIn("beta", md)

        with open(os.path.join(self.data, "repos.json")) as f:
            data = json.load(f)
        by = {r["name"]: r for r in data}
        self.assertEqual(by["alpha"]["summary"], "the alpha service")
        self.assertEqual(by["alpha"]["keywords"], ["edge"])
        self.assertEqual(by["alpha"]["stack"], ["node"])
        self.assertEqual(by["beta"]["stack"], ["go"])

        # rebuild → overrides still applied (never written away by discovery)
        repo_index.build_index([self.root], data_dir=self.data)
        with open(os.path.join(self.data, "repos.json")) as f:
            data2 = json.load(f)
        self.assertEqual({r["name"]: r for r in data2}["alpha"]["summary"], "the alpha service")
        self.assertTrue(os.path.exists(os.path.join(self.data, "repos.overrides.json")))


class StackFromContent(unittest.TestCase):
    """Content-based detection: extension histogram + config/tooling signals,
    unioned with root manifests. `git ls-files` is monkeypatched so the tests are
    fast and don't depend on a real git repo."""
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.repo = os.path.join(self.tmp, "r"); os.makedirs(self.repo)
        self._orig = repo_index._git_ls_files
    def tearDown(self):
        repo_index._git_ls_files = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)
    def _files(self, files):
        repo_index._git_ls_files = lambda repo: list(files)

    def test_extension_histogram(self):
        self._files(["a.sql", "b.sql", "c.sql", "d.py", "e.py", "f.py"])
        st = repo_index._detect_stack(self.repo)
        self.assertIn("sql", st)
        self.assertIn("python", st)

    def test_dominant_below_threshold(self):
        # Only 1 .go file (< threshold) but it's the dominant source ext → still kept.
        self._files(["only.go", "notes.md", "LICENSE"])
        self.assertIn("go", repo_index._detect_stack(self.repo))

    def test_flyway_sql_repo(self):
        # A manifest-less SQL/Flyway repo: previously empty, now [sql, flyway].
        self._files([
            "flyway.conf",
            "db/migration/V1__init.sql", "db/migration/V2__a.sql", "db/migration/V3__b.sql",
        ])
        st = repo_index._detect_stack(self.repo)
        self.assertIn("sql", st)
        self.assertIn("flyway", st)

    def test_tooling_signals(self):
        self._files([
            "Dockerfile",
            ".github/workflows/ci.yml",
            "infra/main.tf", "infra/vars.tf", "infra/out.tf",
        ])
        st = repo_index._detect_stack(self.repo)
        for expected in ("docker", "github-actions", "terraform"):
            self.assertIn(expected, st)

    def test_manifest_unions_with_content(self):
        open(os.path.join(self.repo, "go.mod"), "w").close()
        self._files(["main.go", "util.go", "x.go"])
        self.assertEqual(repo_index._detect_stack(self.repo), ["go"])  # deduped

    def test_swift_repo_detected(self):
        # Previously unmapped (.swift missing from the hand-rolled list); the
        # Linguist-derived map now yields `swift`.
        self._files(["App.swift", "View.swift", "Model.swift"])
        self.assertIn("swift", repo_index._detect_stack(self.repo))

    def test_readme_md_does_not_pollute_stack(self):
        # A plain Python repo with a README.md must yield [python], NOT the
        # bogus `gcc-machine-description` that `.md` used to map to.
        self._files(["README.md", "app.py", "util.py", "test_app.py"])
        st = repo_index._detect_stack(self.repo)
        self.assertEqual(st, ["python"])
        self.assertNotIn("gcc-machine-description", st)


class Fingerprint(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.repo = os.path.join(self.tmp, "r"); os.makedirs(self.repo)
        os.mkdir(os.path.join(self.repo, ".git"))
        with open(os.path.join(self.repo, "README.md"), "w") as f:
            f.write("# Title\nhello world\n")
        os.makedirs(os.path.join(self.repo, "src"))
        with open(os.path.join(self.repo, "src", "deep.py"), "w") as f:
            f.write("x = 1\n")
    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_stable_across_noop(self):
        self.assertEqual(repo_index._fingerprint(self.repo),
                         repo_index._fingerprint(self.repo))

    def test_changes_on_readme(self):
        before = repo_index._fingerprint(self.repo)
        with open(os.path.join(self.repo, "README.md"), "w") as f:
            f.write("# Title\nCOMPLETELY DIFFERENT\n")
        self.assertNotEqual(before, repo_index._fingerprint(self.repo))

    def test_changes_on_toplevel(self):
        before = repo_index._fingerprint(self.repo)
        open(os.path.join(self.repo, "NEWFILE.txt"), "w").close()
        self.assertNotEqual(before, repo_index._fingerprint(self.repo))

    def test_changes_on_manifest_content(self):
        with open(os.path.join(self.repo, "go.mod"), "w") as f:
            f.write("module a\n")
        before = repo_index._fingerprint(self.repo)
        with open(os.path.join(self.repo, "go.mod"), "w") as f:
            f.write("module b\n")
        self.assertNotEqual(before, repo_index._fingerprint(self.repo))

    def test_stable_on_deep_edit(self):
        # An ordinary commit deep in the tree must NOT move the fingerprint.
        before = repo_index._fingerprint(self.repo)
        with open(os.path.join(self.repo, "src", "deep.py"), "w") as f:
            f.write("y = 2\nz = 3\n")
        self.assertEqual(before, repo_index._fingerprint(self.repo))


class Enrichment(unittest.TestCase):
    """Fingerprint-gated, degradable model enrichment. The model-call seam
    (`repo_index._call_model`) is monkeypatched in EVERY test — a real model is
    never contacted."""
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = os.path.join(self.tmp, "ws"); os.makedirs(self.root)
        self.data = os.path.join(self.tmp, "data"); os.makedirs(self.data)
        self.repo = os.path.join(self.root, "acme"); os.makedirs(self.repo)
        os.mkdir(os.path.join(self.repo, ".git"))
        with open(os.path.join(self.repo, "README.md"), "w") as f:
            f.write("# Acme\n\nAcme processes billing events for the platform.\n\n## Setup\nrun it\n")
        self._orig = repo_index._call_model
        self.calls = []
    def tearDown(self):
        repo_index._call_model = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _mock_ok(self, summary="model summary", keywords=("billing", "events")):
        def fake(prompt, **kw):
            self.calls.append(prompt)
            return json.dumps({"result": json.dumps(
                {"summary": summary, "keywords": list(keywords)})})
        repo_index._call_model = fake

    def _mock_fail(self):
        def boom(prompt, **kw):
            self.calls.append(prompt)
            raise RuntimeError("no model available")
        repo_index._call_model = boom

    def _acme(self, repos):
        return {r["name"]: r for r in repos}["acme"]

    def test_llm_used_when_enabled(self):
        self._mock_ok()
        repos = repo_index.build_index([self.root], data_dir=self.data, use_llm=True)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self._acme(repos)["summary"], "model summary")
        self.assertEqual(self._acme(repos)["keywords"], ["billing", "events"])

    def test_gated_no_call_when_unchanged(self):
        self._mock_ok()
        repo_index.build_index([self.root], data_dir=self.data, use_llm=True)
        self.assertEqual(len(self.calls), 1)
        # Second refresh, identical fingerprint → reuse cache, ZERO new calls.
        repos = repo_index.build_index([self.root], data_dir=self.data, use_llm=True)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self._acme(repos)["summary"], "model summary")

    def test_fingerprint_change_retriggers(self):
        self._mock_ok()
        repo_index.build_index([self.root], data_dir=self.data, use_llm=True)
        self.assertEqual(len(self.calls), 1)
        # Structural change (README content) moves the fingerprint → one new call.
        with open(os.path.join(self.repo, "README.md"), "a") as f:
            f.write("\nappended structural change\n")
        repo_index.build_index([self.root], data_dir=self.data, use_llm=True)
        self.assertEqual(len(self.calls), 2)

    def test_degradable_fallback_on_failure(self):
        self._mock_fail()
        repos = repo_index.build_index([self.root], data_dir=self.data, use_llm=True)
        self.assertTrue(self.calls)                       # it attempted the model
        acme = self._acme(repos)
        # Falls back to the README's first non-heading paragraph, deterministically.
        self.assertIn("Acme processes billing events", acme["summary"])
        self.assertTrue(acme["keywords"])

    def test_no_llm_skips_model(self):
        self._mock_ok()
        repos = repo_index.build_index([self.root], data_dir=self.data, use_llm=False)
        self.assertEqual(len(self.calls), 0)
        self.assertIn("Acme processes billing events", self._acme(repos)["summary"])

    def test_override_beats_model(self):
        self._mock_ok(summary="model summary")
        with open(os.path.join(self.data, "repos.overrides.json"), "w") as f:
            json.dump({"acme": {"summary": "hand written summary"}}, f)
        repos = repo_index.build_index([self.root], data_dir=self.data, use_llm=True)
        self.assertEqual(self._acme(repos)["summary"], "hand written summary")
        self.assertEqual(len(self.calls), 0)              # override → enrichment skipped

    def test_model_beats_fallback(self):
        self._mock_ok(summary="model summary")
        repos = repo_index.build_index([self.root], data_dir=self.data, use_llm=True)
        # Distinct from the deterministic README paragraph → proves model won.
        self.assertEqual(self._acme(repos)["summary"], "model summary")
        self.assertNotIn("Acme processes billing events", self._acme(repos)["summary"])

    def test_fingerprint_persisted_in_json(self):
        self._mock_ok()
        repo_index.build_index([self.root], data_dir=self.data, use_llm=True)
        with open(os.path.join(self.data, "repos.json")) as f:
            data = json.load(f)
        self.assertEqual(len(self._acme(data)["fingerprint"]), 12)


class EnrichmentParsing(unittest.TestCase):
    def test_unwrap_cli_envelope(self):
        raw = json.dumps({"result": '{"summary": "s", "keywords": ["a"]}', "type": "result"})
        self.assertEqual(repo_index._unwrap_cli_json(raw), '{"summary": "s", "keywords": ["a"]}')

    def test_extract_json_with_surrounding_prose(self):
        obj = repo_index._extract_json_object('sure!\n{"summary": "s", "keywords": ["a","b"]}\nthanks')
        self.assertEqual(obj["summary"], "s")
        self.assertEqual(obj["keywords"], ["a", "b"])

    def test_extract_json_none_on_garbage(self):
        self.assertIsNone(repo_index._extract_json_object("no json here"))


class CommandDegradable(unittest.TestCase):
    """End-to-end at the CLI layer: a refresh whose model call fails must still
    exit cleanly (no exception) and write a deterministic index."""
    def setUp(self):
        import importlib.util
        lib = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib")
        spec = importlib.util.spec_from_file_location("task_station_cd", os.path.join(lib, "task-station.py"))
        self.ts = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.ts)
        self.tmp = tempfile.mkdtemp()
        self.root = os.path.join(self.tmp, "ws"); os.makedirs(self.root)
        self.data = os.path.join(self.tmp, "data"); os.makedirs(self.data)
        self.repo = os.path.join(self.root, "acme"); os.makedirs(self.repo)
        os.mkdir(os.path.join(self.repo, ".git"))
        with open(os.path.join(self.repo, "README.md"), "w") as f:
            f.write("# Acme\n\nAcme does things.\n")
        self._home = os.environ.get("TASK_STATION_HOME")
        os.environ["TASK_STATION_HOME"] = self.data
        self._orig_call = repo_index._call_model
        self._orig_roots = None
        import config
        self.config = config
        self._orig_roots = config.repo_roots
        config.repo_roots = lambda: [self.root]
    def tearDown(self):
        repo_index._call_model = self._orig_call
        self.config.repo_roots = self._orig_roots
        if self._home is not None:
            os.environ["TASK_STATION_HOME"] = self._home
        shutil.rmtree(self.tmp, ignore_errors=True)

    class _Args:
        def __init__(self, **kw):
            d = dict(terms=[], refresh=False, force=False, json=False, quiet=False, no_llm=False)
            d.update(kw); self.__dict__.update(d)

    def test_refresh_exits_clean_when_model_fails(self):
        def boom(prompt, **kw):
            raise RuntimeError("model down")
        repo_index._call_model = boom
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.ts.cmd_repos(self._Args(refresh=True, quiet=True))  # returns, does not raise
        with open(os.path.join(self.data, "repos.json")) as f:
            data = json.load(f)
        acme = {r["name"]: r for r in data}["acme"]
        self.assertIn("Acme does things", acme["summary"])      # deterministic fallback


if __name__ == "__main__":
    unittest.main()
