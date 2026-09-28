"""Exercise real Git checkouts and deploy subprocesses with an isolated Docker double."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "deploy.py"
DOCKER = r'''#!/usr/bin/env python3
import hashlib
import json
import os
from pathlib import Path
import sys

path = Path(os.environ["FAKE_DOCKER_STATE"])
state = json.loads(path.read_text())
args = sys.argv[1:]
state["calls"].append(args)

def finish(code=0, output=""):
    path.write_text(json.dumps(state))
    print(output)
    sys.exit(code)

if args[0] == "inspect":
    finish(output=json.dumps([state["container"]]))
if args[0] == "build":
    if state.get("fail_build"):
        finish(1)
    tag = args[args.index("--tag") + 1]
    state["images"][tag] = "sha256:" + hashlib.sha256(tag.encode()).hexdigest()
    finish()
if args[:2] == ["image", "inspect"]:
    finish(output=state["images"][args[2]])
if args[:2] == ["image", "tag"]:
    state["images"][args[3]] = state["images"].get(args[2], args[2])
    finish()
if args[:2] == ["image", "ls"]:
    finish(output="\n".join(state["images"]))
if args[:2] == ["image", "rm"]:
    del state["images"][args[2]]
    finish()
if args[0] == "run":
    finish(1 if state.get("fail_tests") and "unittest" in args else 0)
if args[0] == "stop":
    state["container"]["State"]["Running"] = False
    finish()
if args[0] == "compose":
    if "config" in args:
        finish()
    override = [args[index + 1] for index, value in enumerate(args) if value == "-f"][-1]
    tag = json.loads(Path(override).read_text())["services"]["bot"]["image"]
    state["container"] = {
        "Image": state["images"][tag], "State": {"Running": True},
        "RestartCount": int(bool(state.get("fail_start") and ":git-" in tag)),
    }
    finish()
if args[0] == "logs":
    finish(output="INFO aiogram.dispatcher: Run polling for bot @example_bot")
finish(2, "Unsupported fake Docker command: " + str(args))
'''


class DeploymentTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.seed = self.root / "seed"
        self.app = self.root / "app"
        self.origin = self.root / "origin.git"
        self.state = self.root / "state"
        self.docker_state = self.root / "docker.json"
        self.git("init", "--bare", self.origin)
        self.git("init", "-b", "main", self.seed)
        self.git("-C", self.seed, "config", "user.name", "Deploy test")
        self.git("-C", self.seed, "config", "user.email", "test@example.invalid")
        (self.seed / "docker-compose.yml").write_text("services:\n  bot:\n    build: .\n")
        (self.seed / ".gitignore").write_text(".env\ndata/\n")
        (self.seed / "Dockerfile").write_text("FROM scratch\n")
        (self.seed / "tests").mkdir()
        (self.seed / "tests" / "test_bot.py").write_text("# Isolated fixture\n")
        self.commit("initial")
        self.git("-C", self.seed, "remote", "add", "origin", self.origin)
        self.git("-C", self.seed, "push", "origin", "main")
        self.git("clone", "--branch", "main", self.origin, self.app)
        self.previous = self.git("-C", self.app, "rev-parse", "HEAD")
        (self.app / ".env").write_text("SECRET=keep-this-on-server\n")
        (self.app / "data").mkdir()
        (self.app / "data" / "state.json").write_text('{"user_setting": "preserve"}\n')
        binary = self.root / "bin"
        binary.mkdir()
        (binary / "docker").write_text(DOCKER)
        (binary / "docker").chmod(0o755)
        self.env = {
            **os.environ, "APP_DIR": str(self.app), "DEPLOY_STATE_DIR": str(self.state),
            "DEPLOY_BRANCH": "main", "DEPLOY_STABILITY_SECONDS": "0",
            "DEPLOY_HEALTH_TIMEOUT": "5", "FAKE_DOCKER_STATE": str(self.docker_state),
            "PATH": str(binary) + os.pathsep + os.environ["PATH"],
        }
        self.docker_state.write_text(json.dumps({
            "calls": [], "images": {"freelancenotifycation-bot:latest": "sha256:old"},
            "container": {"Image": "sha256:old", "State": {"Running": True}, "RestartCount": 0},
        }))

    def git(self, *args):
        return subprocess.check_output(
            ["git", *map(str, args)], text=True, stderr=subprocess.DEVNULL,
        ).strip()

    def commit(self, message):
        self.git("-C", self.seed, "add", ".")
        self.git("-C", self.seed, "commit", "-m", message)

    def push(self, text="new version"):
        (self.seed / "version.txt").write_text(text)
        self.commit(text)
        self.git("-C", self.seed, "push", "origin", "main")
        return self.git("-C", self.seed, "rev-parse", "HEAD")

    def docker(self, **updates):
        state = json.loads(self.docker_state.read_text())
        state.update(updates)
        self.docker_state.write_text(json.dumps(state))
        return state

    def deploy(self, *args, success=True):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), *args], env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30,
        )
        self.assertEqual(result.returncode == 0, success, result.stdout)
        return result

    def assert_previous_running(self):
        self.assertEqual(self.git("-C", self.app, "rev-parse", "HEAD"), self.previous)
        self.assertEqual(self.docker()["container"]["Image"], "sha256:old")
        self.assertTrue(self.docker()["container"]["State"]["Running"])
        self.assertEqual((self.app / "data" / "state.json").read_text(), '{"user_setting": "preserve"}\n')
        self.assertEqual((self.app / ".env").read_text(), "SECRET=keep-this-on-server\n")

    def test_push_deploys_and_unchanged_revision_does_not_restart(self):
        target = self.push()
        self.deploy()
        self.assertEqual(self.git("-C", self.app, "rev-parse", "HEAD"), target)
        deployed = json.loads((self.state / "deployed.json").read_text())
        self.assertEqual(deployed["revision"], target)
        self.assertEqual(deployed["image"], self.docker()["container"]["Image"])
        with tarfile.open(Path(deployed["backup"]) / "data-and-env.tar.gz") as backup:
            self.assertIn("data/state.json", backup.getnames())
            self.assertIn(".env", backup.getnames())
        calls = self.docker()["calls"]
        test_index = next(i for i, call in enumerate(calls) if "unittest" in call)
        stop_index = next(i for i, call in enumerate(calls) if call[0] == "stop")
        self.assertLess(test_index, stop_index)
        self.deploy()
        self.assertEqual(calls, self.docker()["calls"])

    def test_failed_build_keeps_old_bot_and_quarantines_commit_until_retry(self):
        self.push()
        self.docker(fail_build=True)
        self.deploy(success=False)
        self.assert_previous_running()
        self.assertFalse(any(call[0] == "stop" for call in self.docker()["calls"]))
        calls = self.docker()["calls"]
        self.deploy()
        self.assertEqual(calls, self.docker()["calls"])
        self.docker(fail_build=False)
        self.deploy("--retry")
        self.assertFalse((self.state / "failed.json").exists())

    def test_failed_tests_do_not_stop_current_bot(self):
        self.push()
        self.docker(fail_tests=True)
        self.deploy(success=False)
        self.assert_previous_running()
        self.assertFalse(any(call[0] == "stop" for call in self.docker()["calls"]))

    def test_failed_start_restores_image_code_and_preserves_data(self):
        self.push()
        self.docker(fail_start=True)
        self.deploy(success=False)
        self.assert_previous_running()
        self.assertTrue((self.state / "failed.json").exists())
        self.assertFalse((self.state / "deployed.json").exists())
        self.docker(fail_start=False)
        target = self.push("fixed version")
        self.deploy()
        self.assertEqual(self.git("-C", self.app, "rev-parse", "HEAD"), target)

    def test_dirty_server_checkout_is_never_overwritten(self):
        self.push()
        (self.app / "Dockerfile").write_text("local changes\n")
        self.deploy(success=False)
        self.assert_previous_running()
        self.assertEqual((self.app / "Dockerfile").read_text(), "local changes\n")
        self.assertEqual(self.docker()["calls"], [])

    def test_rewound_main_is_rejected(self):
        self.push()
        self.deploy()
        running = self.git("-C", self.app, "rev-parse", "HEAD")
        calls = self.docker()["calls"]
        self.git("-C", self.seed, "push", "--force", "origin", self.previous + ":main")
        self.deploy(success=False)
        self.assertEqual(self.git("-C", self.app, "rev-parse", "HEAD"), running)
        self.assertEqual(self.docker()["calls"], calls)


if __name__ == "__main__":
    unittest.main()
