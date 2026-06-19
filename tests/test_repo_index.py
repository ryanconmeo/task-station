import os, sys, json, tempfile, shutil, unittest
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
import repo_index


def _load_ts():
    """Load the (hyphenated) task-station.py CLI module for end-to-end CLI tests."""
    import importlib.util
    lib = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib")
    spec = importlib.util.spec_from_file_location("task_station_cli", os.path.join(lib, "task-station.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _repos_args(**kw):
    """A stand-in for the parsed `repos` argparse namespace."""
    class _A:
        pass
    a = _A()
    d = dict(terms=[], refresh=False, force=False, json=False, quiet=False,
             no_llm=False, dry_run=False, re_summarize=False,
             detect_roots=False, set_roots=None)
    d.update(kw)
    a.__dict__.update(d)
    return a


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
        # Enrichment is opt-in: these tests exercise the model path, so acme is
        # explicitly flagged enrich:true in the manifest (the default is false).
        repo_index.save_manifest(self.data, {"acme": {"index": True, "enrich": True}})
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
            d = dict(terms=[], refresh=False, force=False, json=False, quiet=False,
                     no_llm=False, dry_run=False, re_summarize=False,
                     detect_roots=False, set_roots=None)
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


class OptInEnrichment(unittest.TestCase):
    """The core privacy guarantee: enrichment (model egress) is OFF by default and
    only fires for a repo explicitly flagged `enrich:true`. `_call_model` is
    monkeypatched in EVERY test — a real model is never contacted."""
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = os.path.join(self.tmp, "ws"); os.makedirs(self.root)
        self.data = os.path.join(self.tmp, "data"); os.makedirs(self.data)
        for n in ("alpha", "beta"):
            r = _mkrepo(self.root, n)
            with open(os.path.join(r, "README.md"), "w") as f:
                f.write("# %s\n\n%s does things.\n" % (n, n))
        self._orig = repo_index._call_model
        self.calls = []
        def fake(prompt, **kw):
            self.calls.append(prompt)
            return json.dumps({"result": json.dumps({"summary": "m", "keywords": ["k"]})})
        repo_index._call_model = fake
    def tearDown(self):
        repo_index._call_model = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _set(self, name, **flags):
        m = repo_index.load_manifest(self.data)
        m.setdefault(name, {"index": True, "enrich": False}).update(flags)
        repo_index.save_manifest(self.data, m)

    def test_default_refresh_zero_model_calls(self):
        # Even with use_llm=True, all repos default enrich:false → NOTHING is sent.
        repo_index.build_index([self.root], data_dir=self.data, use_llm=True)
        self.assertEqual(len(self.calls), 0)

    def test_enrich_flag_triggers_exactly_one_call(self):
        repo_index.build_index([self.root], data_dir=self.data, use_llm=True)
        self.assertEqual(len(self.calls), 0)
        self._set("alpha", enrich=True)
        egress = []
        repo_index.build_index([self.root], data_dir=self.data, use_llm=True, egress=egress)
        self.assertEqual(len(self.calls), 1)        # exactly one repo enriched
        self.assertEqual(egress, ["alpha"])         # and we recorded WHICH

    def test_manifest_auto_adds_and_prunes(self):
        repo_index.build_index([self.root], data_dir=self.data)
        m = repo_index.load_manifest(self.data)
        self.assertEqual(set(m), {"alpha", "beta"})
        self.assertTrue(m["alpha"]["index"])
        self.assertFalse(m["alpha"]["enrich"])      # safe default
        _mkrepo(self.root, "gamma")
        shutil.rmtree(os.path.join(self.root, "alpha"))
        repo_index.build_index([self.root], data_dir=self.data)
        m2 = repo_index.load_manifest(self.data)
        self.assertEqual(set(m2), {"beta", "gamma"})  # alpha pruned, gamma added

    def test_manifest_preserves_existing_flags(self):
        repo_index.build_index([self.root], data_dir=self.data)
        self._set("beta", enrich=True)
        self._set("alpha", index=False)
        repo_index.build_index([self.root], data_dir=self.data)
        m = repo_index.load_manifest(self.data)
        self.assertTrue(m["beta"]["enrich"])
        self.assertFalse(m["alpha"]["index"])

    def test_index_false_absent_from_outputs(self):
        repo_index.build_index([self.root], data_dir=self.data)
        self._set("beta", index=False)
        repos = repo_index.build_index([self.root], data_dir=self.data)
        self.assertEqual([r["name"] for r in repos], ["alpha"])
        with open(os.path.join(self.data, "repos.json")) as f:
            self.assertNotIn("beta", [r["name"] for r in json.load(f)])
        with open(os.path.join(self.data, "repos.md")) as f:
            self.assertNotIn("## beta", f.read())

    def test_deterministic_refresh_preserves_summary(self):
        repo_index.build_index([self.root], data_dir=self.data)
        self._set("alpha", enrich=True)
        repos = repo_index.build_index([self.root], data_dir=self.data, use_llm=True)
        self.assertEqual({r["name"]: r for r in repos}["alpha"]["summary"], "m")
        # Turn enrich off → a deterministic refresh must NOT clobber the model summary.
        self._set("alpha", enrich=False)
        repos = repo_index.build_index([self.root], data_dir=self.data, use_llm=False)
        self.assertEqual({r["name"]: r for r in repos}["alpha"]["summary"], "m")

    def test_re_summarize_overrides_preservation(self):
        repo_index.build_index([self.root], data_dir=self.data)
        self._set("alpha", enrich=True)
        repo_index.build_index([self.root], data_dir=self.data, use_llm=True)  # "m"
        self._set("alpha", enrich=False)
        repos = repo_index.build_index([self.root], data_dir=self.data,
                                       use_llm=False, re_summarize=True)
        self.assertIn("does things", {r["name"]: r for r in repos}["alpha"]["summary"])

    def test_dry_run_sends_nothing(self):
        repo_index.build_index([self.root], data_dir=self.data)
        self._set("alpha", enrich=True)
        egress = []
        repo_index.build_index([self.root], data_dir=self.data, use_llm=True,
                               dry_run=True, egress=egress)
        self.assertEqual(len(self.calls), 0)        # dry run: NOTHING sent
        self.assertEqual(egress, ["alpha"])         # but reports what WOULD be sent


class MarkerExclusion(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = os.path.join(self.tmp, "ws"); os.makedirs(self.root)
        self.data = os.path.join(self.tmp, "data"); os.makedirs(self.data)
    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_marker_excludes_from_discovery_and_index(self):
        _mkrepo(self.root, "alpha")
        beta = _mkrepo(self.root, "beta")
        open(os.path.join(beta, ".task-station-ignore"), "w").close()
        names = sorted(r["name"] for r in repo_index.discover([self.root]))
        self.assertEqual(names, ["alpha"])          # beta excluded from discovery
        repos = repo_index.build_index([self.root], data_dir=self.data)
        self.assertEqual([r["name"] for r in repos], ["alpha"])
        # …and never even appears in the manifest, regardless of any prior entry.
        self.assertNotIn("beta", repo_index.load_manifest(self.data))


class DetectRoots(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def test_detects_workspace_and_dense_dirs(self):
        os.makedirs(os.path.join(self.home, "Workspace"))
        dense = os.path.join(self.home, "projects"); os.makedirs(dense)
        _mkrepo(dense, "r1"); _mkrepo(dense, "r2")        # >=2 git repos → candidate
        sparse = os.path.join(self.home, "misc"); os.makedirs(sparse)
        _mkrepo(sparse, "only")                           # 1 git repo → NOT a candidate
        roots = repo_index.detect_roots(home=self.home)
        self.assertIn(os.path.join(self.home, "Workspace"), roots)
        self.assertIn(dense, roots)
        self.assertNotIn(sparse, roots)


class EgressGuard(unittest.TestCase):
    """The enrichment prompt must never carry secret-bearing file CONTENTS — and
    the denylist additionally keeps their NAMES out of the tree sketch."""
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.repo = os.path.join(self.tmp, "r"); os.makedirs(self.repo)
        with open(os.path.join(self.repo, "README.md"), "w") as f:
            f.write("# R\n\nA service.\n")
        with open(os.path.join(self.repo, ".env"), "w") as f:
            f.write("API_TOKEN=supersecretvalue123\n")
        with open(os.path.join(self.repo, "secrets.yaml"), "w") as f:
            f.write("password: hunter2\n")
        self._orig = repo_index._git_ls_files
        repo_index._git_ls_files = lambda repo: [
            "README.md", "app.py", ".env", "secrets.yaml", "deploy/tls.pem", ".npmrc"]
    def tearDown(self):
        repo_index._git_ls_files = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_secret_contents_and_names_never_in_prompt(self):
        prompt = repo_index._build_prompt(
            {"name": "r", "path": self.repo, "ado_project": None, "stack": ["python"]})
        for leaked in ("supersecretvalue123", "hunter2"):
            self.assertNotIn(leaked, prompt)          # contents never read
        for name in (".env", "secrets.yaml", "tls.pem", ".npmrc"):
            self.assertNotIn(name, prompt)            # names filtered from the sketch
        self.assertIn("app.py", prompt)               # benign names survive

    def test_is_sensitive_name(self):
        for s in (".env", ".env.local", "secrets.json", "credentials.yml",
                  "server.key", "cert.pem", "id_rsa", ".npmrc", "db-password.txt"):
            self.assertTrue(repo_index._is_sensitive_name(s), s)
        for ok in ("app.py", "README.md", "main.go", "Dockerfile"):
            self.assertFalse(repo_index._is_sensitive_name(ok), ok)


class ReposCLIToggles(unittest.TestCase):
    """End-to-end CLI: the no-JSON-editing toggle/onboarding surface."""
    def setUp(self):
        self.ts = _load_ts()
        self.tmp = tempfile.mkdtemp()
        self.root = os.path.join(self.tmp, "ws"); os.makedirs(self.root)
        self.data = os.path.join(self.tmp, "data"); os.makedirs(self.data)
        for n in ("alpha", "beta"):
            r = _mkrepo(self.root, n)
            open(os.path.join(r, "README.md"), "w").close()
        self._home = os.environ.get("TASK_STATION_HOME")
        os.environ["TASK_STATION_HOME"] = self.data
        import config
        self.config = config
        self._orig_roots = config.repo_roots
        config.repo_roots = lambda: [self.root]
    def tearDown(self):
        self.config.repo_roots = self._orig_roots
        if self._home is not None:
            os.environ["TASK_STATION_HOME"] = self._home
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, **kw):
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.ts.cmd_repos(_repos_args(**kw))
        return buf.getvalue()

    def test_refresh_reports_nothing_sent(self):
        out = self._run(refresh=True, quiet=True)
        self.assertIn("sent: nothing", out)
        self.assertEqual(set(repo_index.load_manifest(self.data)), {"alpha", "beta"})

    def test_exclude_then_absent(self):
        self._run(refresh=True, quiet=True)
        out = self._run(terms=["exclude", "beta"])
        self.assertIn("excluded", out)
        self.assertFalse(repo_index.load_manifest(self.data)["beta"]["index"])
        self._run(refresh=True, quiet=True)
        with open(os.path.join(self.data, "repos.json")) as f:
            self.assertNotIn("beta", [r["name"] for r in json.load(f)])

    def test_include_path_accepted(self):
        self._run(refresh=True, quiet=True)
        self._run(terms=["exclude", "beta"])
        self._run(terms=["include", os.path.join(self.root, "beta")])  # path, not name
        self.assertTrue(repo_index.load_manifest(self.data)["beta"]["index"])

    def test_enrich_toggle(self):
        self._run(refresh=True, quiet=True)
        self._run(terms=["enrich", "alpha"])
        self.assertTrue(repo_index.load_manifest(self.data)["alpha"]["enrich"])
        self._run(terms=["enrich", "alpha", "off"])
        self.assertFalse(repo_index.load_manifest(self.data)["alpha"]["enrich"])

    def test_config_lists_all(self):
        self._run(refresh=True, quiet=True)
        out = self._run(terms=["config"])
        self.assertIn("alpha", out)
        self.assertIn("beta", out)

    def test_unknown_name_message(self):
        self._run(refresh=True, quiet=True)
        out = self._run(terms=["include", "does-not-exist"])
        self.assertIn("no repo named", out)

    def test_set_roots_persists(self):
        self._run(set_roots="/a/x,/b/y")
        self.assertEqual(self.config.get("repo_roots"), ["/a/x", "/b/y"])

    def test_first_run_onboarding(self):
        # No roots configured AND no manifest yet → onboarding, no scan.
        out = self._run()
        self.assertIn("first-run setup", out)
        self.assertIn("OFF by default", out)
        self.assertFalse(os.path.exists(os.path.join(self.data, "repos.json")))


if __name__ == "__main__":
    unittest.main()
