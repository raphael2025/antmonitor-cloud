"""Installer source selection without package installs, network or systemd."""

import io
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest


INSTALLER = Path(__file__).resolve().parents[1] / "install.sh"
GITHUB = "https://github.com/raphael2025/antmonitor-cloud.git"
GITEE = "https://gitee.com/raphael01/antmonitor-cloud.git"


@unittest.skipUnless(shutil.which("bash") and shutil.which("tar"), "requires bash and tar")
class InstallSourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.install_dir = self.root / "installed"
        self.log = self.root / "requests.log"
        self.archive = self.root / "fixture.tar.gz"
        with tarfile.open(self.archive, "w:gz") as archive:
            payload = b"# fixture source\n"
            entry = tarfile.TarInfo("fixture/server.py")
            entry.size = len(payload)
            archive.addfile(entry, io.BytesIO(payload))
            entry = tarfile.TarInfo("fixture/requirements.txt")
            entry.size = 0
            archive.addfile(entry, io.BytesIO(b""))

        # Load the real argument parser and functions; omit only the root guard
        # and the final action dispatch, which would mutate the test machine.
        source = INSTALLER.read_text(encoding="utf-8")
        library = source.rsplit('\ncase "$ACTION" in\n', 1)[0]
        library = "\n".join(
            line for line in library.splitlines()
            if not line.startswith('[[ "${EUID}" -eq 0 ]] || die ')
        )
        self.library = self.root / "functions.sh"
        self.library.write_text(library + "\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def run_script(self, body, *args, origin=GITHUB, clone_status=0,
                   fetch_status=0, extra_env=None, no_git=False):
        env = {
            key: value for key, value in os.environ.items()
            if not key.startswith("ANTCLOUD_") and key != "GH_PROXY"
        }
        env.update({
            "ANTCLOUD_HOME": str(self.install_dir),
            "TEST_LIBRARY": str(self.library),
            "TEST_LOG": str(self.log),
            "TEST_ARCHIVE": str(self.archive),
            "TEST_ORIGIN": origin,
            "TEST_CLONE_STATUS": str(clone_status),
            "TEST_FETCH_STATUS": str(fetch_status),
            "LANG": "C",
        })
        if extra_env:
            env.update(extra_env)
        stubs = r'''
source "$TEST_LIBRARY" "$@"
as_svc() { "$@"; }
prepare_dir() { mkdir -p "$DIR"; }
give_tmp() { :; }
git() {
  printf 'git %s\n' "$*" >> "$TEST_LOG"
  if [[ "$1" == "clone" ]]; then
    [[ "$TEST_CLONE_STATUS" == "0" ]] || return "$TEST_CLONE_STATUS"
    [[ "${TEST_GITHUB_FAIL:-}" != 1 || "$*" != *github.com* ]] || return 1
    local target="${@: -1}"
    mkdir -p "$target/.git"
    printf '# cloned source\n' > "$target/server.py"
    return 0
  fi
  if [[ "$1" == "-C" ]]; then shift 2; fi
  case "$1" in
    remote)
      case "$2" in
        get-url) printf '%s\n' "$TEST_ORIGIN" ;;
        set-url) TEST_ORIGIN="$4" ;;
        *) return 97 ;;
      esac ;;
    fetch)
      [[ "${TEST_ORIGIN_FAIL:-}" != 1 || "$*" != *origin* ]] || return 1
      return "$TEST_FETCH_STATUS" ;;
    diff) return "${TEST_DIRTY:-0}" ;;
    rev-parse) printf 'master\n' ;;
    merge) return 0 ;;
    *) return 98 ;;
  esac
}
curl() {
  printf 'curl %s\n' "$*" >> "$TEST_LOG"
  [[ "${TEST_CURL_FAIL:-}" != 1 ]] || return 22
  cat "$TEST_ARCHIVE"
}
'''
        if no_git:
            stubs += r'''
command() {
  if [[ "$1" == "-v" && "${2:-}" == "git" ]]; then return 1; fi
  builtin command "$@"
}
'''
        return subprocess.run(
            ["bash", "-c", stubs + "\n" + body, "installer-test", *args],
            cwd=self.root, env=env, text=True, capture_output=True, timeout=10,
        )

    def requests(self):
        return self.log.read_text() if self.log.exists() else ""

    def existing_checkout(self):
        (self.install_dir / ".git").mkdir(parents=True)

    def test_gitee_install_clones_selected_branch(self):
        result = self.run_script("fetch_code", "--source", "gitee", "--branch", "release")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("git clone -q --branch release " + GITEE, self.requests())
        self.assertTrue((self.install_dir / ".git").is_dir())
        self.assertNotIn("github.com", self.requests())

    def test_auto_uses_gitee_when_github_is_unreachable(self):
        result = self.run_script("fetch_code", extra_env={"TEST_GITHUB_FAIL": "1"})
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn(GITHUB, self.requests())
        self.assertIn(GITEE, self.requests())
        self.assertNotIn("curl ", self.requests())

    def test_explicit_github_never_clones_gitee(self):
        result = self.run_script("fetch_code", "--source", "github", extra_env={"TEST_GITHUB_FAIL": "1"})
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertNotIn(GITEE, self.requests())

    def test_auto_existing_fallback_preserves_origin_and_data(self):
        self.existing_checkout()
        for name in ("config.yaml", "cloud_settings.json", "cloud.db"):
            (self.install_dir / name).write_bytes(b"data\x00\xff")
        result = self.run_script('fetch_code\nprintf "ORIGIN=%s" "$TEST_ORIGIN"', extra_env={"TEST_ORIGIN_FAIL": "1"})
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("ORIGIN=" + GITHUB, result.stdout)
        self.assertIn("fetch -q " + GITEE, self.requests())
        self.assertNotIn("remote set-url", self.requests())
        for name in ("config.yaml", "cloud_settings.json", "cloud.db"):
            self.assertEqual((self.install_dir / name).read_bytes(), b"data\x00\xff")

    def test_auto_fallback_does_not_touch_custom_or_dirty_checkout(self):
        self.existing_checkout()
        for origin, dirty in (("https://example.org/team/cloud.git", "0"), (GITHUB, "1")):
            self.log.unlink(missing_ok=True)
            result = self.run_script("fetch_code", origin=origin, extra_env={"TEST_ORIGIN_FAIL": "1", "TEST_DIRTY": dirty})
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn(GITEE, self.requests())
            self.assertNotIn("merge ", self.requests())

    def test_both_sources_and_archive_failure_preserve_existing_files(self):
        self.install_dir.mkdir()
        (self.install_dir / "cloud_settings.json").write_bytes(b"keep")
        result = self.run_script("fetch_code", clone_status=1, extra_env={"TEST_CURL_FAIL": "1"})
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.install_dir / "cloud_settings.json").read_bytes(), b"keep")
        self.assertFalse((self.install_dir / "server.py").exists())

    def test_relative_directory_is_resolved_before_cd(self):
        result = self.run_script('cd /\nprintf "%s" "$DIR"', "--dir", ".")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(Path(result.stdout).resolve(), self.root.resolve())

    def test_gitee_clone_failure_does_not_download_archive_or_github(self):
        result = self.run_script("fetch_code", "--source", "gitee", clone_status=1)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("curl ", self.requests())
        self.assertNotIn("github.com", self.requests())
        self.assertFalse(self.install_dir.exists())

    def test_gitee_missing_git_fails_without_archive_download(self):
        result = self.run_script("fetch_code", "--source", "gitee", no_git=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.requests(), "")

    def test_existing_checkout_without_git_is_not_overwritten(self):
        self.existing_checkout()
        result = self.run_script("fetch_code", no_git=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("curl ", self.requests())

    def test_github_clone_failure_keeps_its_archive_fallback(self):
        result = self.run_script("fetch_code", clone_status=1)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn(GITHUB, self.requests())
        self.assertIn("https://github.com/raphael2025/antmonitor-cloud/archive/refs/heads/master.tar.gz", self.requests())
        self.assertTrue((self.install_dir / "server.py").is_file())

    def test_github_custom_repository_and_proxy_remain_supported(self):
        result = self.run_script("fetch_code", clone_status=1, extra_env={
            "ANTCLOUD_REPO": "example/private-cloud",
            "GH_PROXY": "https://mirror.example/",
        })
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("https://mirror.example/https://github.com/example/private-cloud.git", self.requests())
        self.assertIn("https://mirror.example/https://github.com/example/private-cloud/archive/", self.requests())

    def test_default_update_preserves_gitee_and_custom_origins(self):
        self.existing_checkout()
        for origin in (GITEE, "https://example.org/team/cloud.git"):
            with self.subTest(origin=origin):
                result = self.run_script("fetch_code", origin=origin)
                self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                self.assertNotIn("remote set-url", self.requests())
                self.assertNotIn("curl ", self.requests())

    def test_explicit_source_switches_official_origins(self):
        self.existing_checkout()
        for source, origin, target in (("gitee", GITHUB, GITEE), ("github", GITEE, GITHUB)):
            with self.subTest(source=source):
                self.log.unlink(missing_ok=True)
                result = self.run_script("fetch_code", "--source", source, origin=origin)
                self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                self.assertIn("remote set-url origin " + target, self.requests())
                self.assertIn("fetch -q origin master", self.requests())

    def test_explicit_source_refuses_to_overwrite_fork_origin(self):
        self.existing_checkout()
        result = self.run_script("fetch_code", "--source", "gitee", origin="https://example.org/team/cloud.git")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("remote set-url", self.requests())
        self.assertNotIn("fetch ", self.requests())

    def test_failed_update_stops_before_restart_or_fallback(self):
        self.existing_checkout()
        result = self.run_script('fetch_code\nprintf "CONTINUED"', "--source", "gitee", origin=GITEE, fetch_status=1)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("CONTINUED", result.stdout)
        self.assertNotIn("merge ", self.requests())
        self.assertNotIn("curl ", self.requests())

    def test_archive_install_can_move_to_gitee_without_losing_config(self):
        self.install_dir.mkdir()
        (self.install_dir / "server.py").write_text("# old code\n")
        config = self.install_dir / "config.yaml"
        config.write_text("keep: this\n")
        result = self.run_script("fetch_code", "--source", "gitee")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertTrue((self.install_dir / ".git").is_dir())
        self.assertEqual(config.read_text(), "keep: this\n")

    def test_source_environment_and_invalid_arguments(self):
        result = self.run_script("fetch_code", extra_env={"ANTCLOUD_SOURCE": "gitee"})
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn(GITEE, self.requests())
        for args in (("--source", "unknown"), ("--source",)):
            with self.subTest(args=args):
                result = self.run_script("fetch_code", *args)
                self.assertNotEqual(result.returncode, 0)

    def test_explicit_source_takes_priority_over_local_source(self):
        result = self.run_script('SRC="/unused/local/source"\nfetch_code', "--source", "gitee")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn(GITEE, self.requests())
        self.assertTrue((self.install_dir / ".git").is_dir())

    def test_explicit_source_takes_priority_over_in_place_source(self):
        self.install_dir.mkdir()
        (self.install_dir / "server.py").write_text("# old source\n")
        result = self.run_script("INPLACE=1\nfetch_code", "--source", "gitee")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn(GITEE, self.requests())
        self.assertTrue((self.install_dir / ".git").is_dir())

    def test_local_source_without_explicit_platform_keeps_offline_behavior(self):
        result = self.run_script('SRC="/unused/local/source"\ncopy_local_src() { printf "LOCAL_COPY"; }\nfetch_code')
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("LOCAL_COPY", result.stdout)
        self.assertEqual(self.requests(), "")

    def test_gitee_stops_if_system_package_manager_cannot_install_git(self):
        body = r'''
apt-get() {
  [[ "${*: -1}" != "git" ]]
}
install_packages
'''
        result = self.run_script(body, "--source", "gitee", no_git=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("curl ", self.requests())

    def test_existing_git_can_be_used_when_package_install_fails(self):
        body = r'''
apt-get() {
  [[ "${*: -1}" != "git" ]]
}
install_packages
'''
        result = self.run_script(body, "--source", "gitee")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)


if __name__ == "__main__":
    unittest.main()
