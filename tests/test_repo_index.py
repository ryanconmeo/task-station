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


if __name__ == "__main__":
    unittest.main()
