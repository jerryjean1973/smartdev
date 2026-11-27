import base64
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
from uuid import UUID

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
RUNNER_SPEC = importlib.util.spec_from_file_location("smd_task_runner", SCRIPTS / "smd-task-runner.py")
runner = importlib.util.module_from_spec(RUNNER_SPEC)
RUNNER_SPEC.loader.exec_module(runner)

TASK = "603fcce1-12c3-417a-b6f3-d4ac5d2ba28b"
PROJECT = "04ddc860-6b5c-48e6-9b3b-79c9e9795cf7"
INSTANCE = "daa9facc-8714-4cb9-a089-dc2459c89943"
REVISION = "release-17"
ZONE = "deploy.example.test"
SSH_KEY = "7a0f977e-c37f-449e-9553-41ac3972f035"
IPV4 = "8.8.4.4"
PORTAL = f"smd-{UUID(TASK).hex}.{ZONE}"
CODER = f"coder-{UUID(TASK).hex}.{ZONE}"
APP = f"app-{UUID(PROJECT).hex[:12]}-{UUID(TASK).hex[:12]}.{ZONE}"
APP_REVISION = "a" * 40
INSTALLER = b"#!/bin/bash\nprintf 'release-17\\n'\n"


def claim():
    return {
        "id": TASK, "project_id": PROJECT, "claim_token": "test-token",
        "provider_tag": "smd-" + TASK, "region": runner.REGION, "plan": runner.PLAN,
        "os_id": runner.OS_ID, "monthly_cost_usd": runner.MONTHLY_COST_USD,
        "dns_zone": ZONE, "deployment_hostname": PORTAL, "coder_hostname": CODER,
        "ssh_key_id": SSH_KEY,
        "projects": [{"id": PROJECT, "name": "test", "repository": "https://example.test/repo",
                      "hostname": APP, "revision": APP_REVISION, "runtime": "auto"}],
        "installer_url": "https://releases.example.test/installer",
        "installer_sha256": sha256(INSTALLER).hexdigest(), "release_revision": REVISION,
    }


class RunnerTest(unittest.TestCase):
    def setUp(self):
        self.context = TemporaryDirectory()
        self.addCleanup(self.context.cleanup)
        self.root = Path(self.context.name)
        self.path = self.root / "claimed.json"
        self.token = self.root / "token"
        self.token.write_text("test")
        patcher = patch.dict(os.environ, SMARTDEV_VULTR_TOKEN_FILE=str(self.token),
                             SMARTDEV_SMD_VULTR_SSH_KEY_ID=SSH_KEY)
        patcher.start()
        self.addCleanup(patcher.stop)
        config = patch.object(runner.DeploymentDNS, "configured", return_value=Mock())
        self.dns = config.start().return_value
        self.addCleanup(config.stop)

    def test_enrolls_after_durable_provider_state_and_awaits_verified_receipt(self):
        record = {"claim": claim(), "phase": "claimed"}
        runner.save(self.path, record)
        commands = []

        def queue(*args, **kwargs):
            commands.append((args, kwargs))
            if args[0] == "receipt-enrollment":
                persisted = json.loads(self.path.read_text())
                self.assertEqual(persisted["provider"]["instance_id"], INSTANCE)
                self.assertEqual(args[3], persisted["receipt_key"]["public_key"])
                self.assertEqual(args[4:], ("--ipv4", IPV4))
                self.assertEqual(persisted["provider"]["ipv4"], IPV4)
                self.dns.assign.assert_called_once()
            if args[0] == "receipt":
                return {"verified": any(args[0] == "enable-verification" for args, _ in commands)}
            return {}

        def provider(_, task, state, persist, bootstrap, *, ssh_key_id):
            self.assertEqual(ssh_key_id, SSH_KEY)
            self.assertEqual(task, TASK)
            self.assertIn(b"/api/smd-deploy-tasks/" + TASK.encode(), base64.b64decode(bootstrap))
            self.assertIn("receipt_key", json.loads(self.path.read_text()))
            state.update(instance_id=INSTANCE, tag="smd-" + task)
            persist(state)
            return INSTANCE

        with patch.object(runner, "check_installer"), patch.object(runner, "picker", queue), \
                patch.object(runner, "create_or_reconcile", provider), \
                patch.object(runner, "instance_ipv4", return_value=IPV4):
            runner.process(self.path, record)
            self.assertEqual(record["phase"], "awaiting_receipt")
            self.assertNotIn("receipt_key", json.loads(self.path.read_text()))
            self.assertFalse(any(args[:3] == ("progress", TASK, "deployed") for args, _ in commands))
            commands.append((("enable-verification",), {}))
            runner.process(self.path, json.loads(self.path.read_text()))
        self.assertFalse(self.path.exists())
        self.assertEqual(commands[-1][0][:3], ("progress", TASK, "deployed"))

    def test_lost_vultr_response_retains_the_create_intent_and_receipt_key(self):
        record = {"claim": claim(), "phase": "claimed"}
        with patch.object(runner, "check_installer"), patch.object(runner, "picker"):
            runner.prepare(self.path, record)
            key = record["receipt_key"]
            posts = []

            def provider(method, path, body=None):
                if method == "POST":
                    posts.append(body)
                    self.assertTrue(json.loads(self.path.read_text())["provider"]["create_started"])
                    raise OSError("Lost response")
                if path.startswith("/ssh-keys/"):
                    return {"ssh_key": {"id": SSH_KEY, "ssh_key": "ssh-ed25519 key"}}
                if path.startswith("/instances?"):
                    return {"instances": []}
                if path.startswith("/regions/"):
                    return {"available_plans": [runner.PLAN]}
                return {"plans": [{"id": runner.PLAN, "vcpu_count": 4, "ram": 8192,
                                   "monthly_cost": runner.MONTHLY_COST_USD}]}

            with patch.object(runner, "make_request", return_value=provider):
                runner.process(self.path, json.loads(self.path.read_text()))
                runner.process(self.path, json.loads(self.path.read_text()))
            self.assertEqual(len(posts), 1)
            self.assertEqual(json.loads(self.path.read_text())["receipt_key"], key)

    def test_lost_claim_response_replays_the_same_durable_token(self):
        claims = []

        def queue(*args, token=None):
            if args[0] == "list":
                return [{"id": TASK, "status": "queued" if not claims else "processing"}]
            self.assertEqual(args, ("claim", TASK))
            journal = json.loads((self.root / (TASK + ".json")).read_text())
            self.assertEqual(journal, {"phase": "claiming", "task": TASK, "token": token})
            claims.append(token)
            if len(claims) == 1:
                raise OSError("Response lost")
            return {**claim(), "claim_token": token}

        with patch.object(runner, "picker", queue), patch.object(runner, "process") as process:
            with self.assertRaises(OSError):
                runner.cycle(self.root)
            runner.cycle(self.root)
        self.assertEqual(len(claims), 2)
        self.assertEqual(claims[0], claims[1])
        self.assertEqual(process.call_args.args[1]["phase"], "claimed")

    def test_waiting_on_one_deployment_does_not_hold_up_new_requests(self):
        earlier_id = "1fddc049-2bc8-4429-8884-c87574358c13"
        earlier = self.root / (earlier_id + ".json")
        runner.save(earlier, {"claim": {**claim(), "id": earlier_id}, "phase": "awaiting_receipt"})

        def queue(*args, token=None):
            if args[0] == "list":
                return [{"id": TASK, "status": "queued"}]
            return {**claim(), "claim_token": token}

        with patch.object(runner, "picker", queue), patch.object(runner, "process") as process:
            runner.cycle(self.root)
        self.assertEqual([call.args[1]["claim"]["id"] for call in process.call_args_list],
                         [earlier_id, TASK])
        self.assertTrue(earlier.exists())
        self.assertTrue((self.root / (TASK + ".json")).exists())

    def test_dns_conflict_and_ssh_mismatch_block_provider_before_it_runs(self):
        for conflict, overrides in [
            (runner.DNSBlocked("Existing DNS conflicts with deployment."), {}),
            (None, {"ssh_key_id": "e0739211-e435-4f1d-8dca-c04d2995a213"}),
        ]:
            record = {"claim": {**claim(), **overrides}, "phase": "claimed"}
            self.dns.preflight.side_effect = conflict
            with patch.object(runner, "check_installer"), patch.object(runner, "picker") as queue, \
                    patch.object(runner, "make_request") as provider:
                runner.process(self.path, record)
            provider.assert_not_called()
            self.assertEqual(queue.call_args.args[:3], ("progress", TASK, "needs_attention"))

    def test_invalid_release_prevents_any_provider_work(self):
        record = {"claim": {**claim(), "installer_sha256": "invalid"}, "phase": "claimed"}
        with patch.object(runner, "picker") as queue, patch.object(runner, "make_request") as provider:
            runner.process(self.path, record)
        provider.assert_not_called()
        self.assertEqual(queue.call_args.args[:3], ("progress", TASK, "needs_attention"))

    def test_installer_preflight_checks_the_actual_response_content(self):
        class Reply:
            def __enter__(self):
                return self
            def __exit__(self, *_):
                return False
            def read(self, _):
                return INSTALLER
        with patch.object(runner, "build_opener") as opener:
            opener.return_value.open.return_value = Reply()
            runner.check_installer(claim())
            with self.assertRaisesRegex(ValueError, "pinned SHA-256"):
                runner.check_installer({**claim(), "installer_sha256": "0" * 64})

    def test_qa_recovery_key_is_stable_and_only_public_key_reaches_bootstrap(self):
        public = runner.recovery_public_key(self.path, TASK)
        self.assertEqual(public, runner.recovery_public_key(self.path, TASK))
        identity = self.root / "recovery" / TASK
        self.assertEqual(identity.stat().st_mode & 0o777, 0o600)
        receipt = runner.generate_receipt_key()
        script = base64.b64decode(runner.cloud_init(
            claim(), "https://control.example.test/api/receipt", receipt["private_key"],
            ingress=IPV4, recovery_key=public,
        ))
        self.assertIn(" ".join(public.split()[:2]).encode(), script)
        self.assertIn(b'recovery_ingress=8.8.4.4', script)
        self.assertNotIn(b"OPENSSH PRIVATE KEY", script)
        subprocess.run(["bash", "-n"], input=script, check=True)

    def test_cloud_init_signs_verified_installer_output_and_retries_enrollment(self):
        key = runner.generate_receipt_key()
        installer = self.root / "installer"
        installer.write_bytes(INSTALLER)
        fake_curl = self.root / "curl"
        fake_curl.write_text(r"""#!/bin/bash
set -eu
if [[ " $* " == *" --resolve "* ]]; then
    if [[ "${!#}" == */api/deployment-portal/metadata ]]; then
        args=("$@")
        for ((i=0;i<${#args[@]};i++)); do
            if [[ "${args[i]}" == --output ]]; then printf '%s' "$TEST_PORTAL_METADATA" > "${args[i+1]}"; fi
        done
    fi
    if [[ "${!#}" == */.well-known/smartdev-installation && -n "${TEST_INSTALL_MARKER:-}" ]]; then
        args=("$@")
        for ((i=0;i<${#args[@]};i++)); do
            if [[ "${args[i]}" == --output ]]; then printf '%s' "$TEST_INSTALL_MARKER" > "${args[i+1]}"; fi
        done
    fi
    if [[ "${!#}" == "${TEST_UNCONFIGURED_APP:-unused}" ]]; then printf 503; else printf 200; fi
    exit
fi
case "${!#}" in
    */instance-v2-id) printf '%s' "$TEST_INSTANCE" ;;
    */receipt)
        for arg in "$@"; do
            if [[ "$arg" == @* ]]; then cp "${arg:1}" "$TEST_RECEIPT"; fi
        done
        if [[ -e "$TEST_FLAG" ]]; then printf 200; else touch "$TEST_FLAG"; printf 409; fi
        ;;
    *) cp "$TEST_INSTALLER" "${!#}" ;;
esac
""")
        fake_curl.chmod(0o700)
        fake_sleep = self.root / "sleep"
        fake_sleep.write_text("#!/bin/bash\nexit 0\n")
        fake_sleep.chmod(0o700)
        fake_ip = self.root / "ip"
        fake_ip.write_text("#!/bin/bash\ncat <<'IP'\n" +
                           json.dumps([{"addr_info": [{"local": IPV4}]}]) + "\nIP\n")
        fake_ip.chmod(0o700)
        (self.root / "sitecustomize.py").write_text(
            "import io, json, os, pathlib, socket, urllib.request\n"
            "from email.message import Message\n"
            "import time; time.sleep = lambda seconds: None\n"
            "class FakeOpener:\n"
            "    def open(self, request, **kwargs):\n"
            "        pathlib.Path(os.environ['TEST_GIT_REQUEST']).write_bytes(request.data)\n"
            "        if json.loads(request.data).get('artifact') == 'qa-runtime':\n"
            "            response = io.BytesIO(b'LICENSE_KEY=fixture-runtime\\n')\n"
            "            response.headers = Message()\n"
            "            response.headers['Content-Type'] = 'application/x-smd-runtime-env'\n"
            "            return response\n"
            "        if json.loads(request.data).get('artifact') == 'build-context':\n"
            "            response = io.BytesIO(b'fixture-context')\n"
            "            response.headers = Message()\n"
            "            response.headers['Content-Type'] = 'application/gzip'\n"
            "            return response\n"
            "        if os.getenv('TEST_GIT_BUNDLE'):\n"
            "            response = io.BytesIO(b'fixture-bundle')\n"
            "            response.headers = Message()\n"
            "            response.headers['Content-Type'] = 'application/x-git-bundle'\n"
            "            return response\n"
            "        return io.BytesIO(b'{\"token\":\"fixture-token\"}')\n"
            "urllib.request.build_opener = lambda *args: FakeOpener()\n"
            "socket.getaddrinfo = lambda *args: [(None, None, None, None, ('8.8.4.4', 443))]\n"
        )
        # Exercise the real bootstrap payload and arguments, with Docker installation isolated from this shell test.
        fake_python = self.root / "python3"
        fake_python.write_text("""#!/bin/bash
if [[ "$1" == */enroll_deployment_portal.py ]]; then
    [[ -s "$1" && -s "$7/deployment_portal_receiver.py" && -s "$7/portal_handoff.py" &&
       "$2" == "$TEST_PROJECT" && "$3" == "$TEST_TASK" && "$4" == "$TEST_INSTANCE" &&
       "$5" == "https://control.example.test" && "$6" == "$TEST_PORTAL" ]]
    exit $?
fi
exec """ + sys.executable + ' "$@"\n')
        fake_python.chmod(0o700)
        metadata = {"issuer":"https://control.example.test","portal":"https://"+PORTAL,"account":"owner",
                    "task_id":TASK,"instance_id":INSTANCE,"protocol":"owner-pkce-v1"}
        captured = self.root / "receipt.json"
        environment = dict(os.environ, PATH=f"{self.root}:{os.environ['PATH']}",
                           PYTHONPATH=str(self.root), TEST_INSTALLER=str(installer), TEST_INSTANCE=INSTANCE,
                           TEST_PORTAL_METADATA=json.dumps(metadata), TEST_PROJECT=PROJECT, TEST_TASK=TASK, TEST_PORTAL="https://"+PORTAL,
                           TEST_RECEIPT=str(captured), TEST_FLAG=str(self.root / "retried"),
                           TEST_GIT_REQUEST=str(self.root / "git-request"))
        def bootstrap(deployment):
            script = base64.b64decode(runner.cloud_init(
                deployment, "https://control.example.test/api/receipt", key["private_key"],
            ))
            script = script.replace(b"/opt/smartdev", os.fsencode(self.root / "smd"))
            return script.replace(b"/run/smartdev-git", os.fsencode(self.root / "smd-git"))

        script = bootstrap(claim())
        evidence = self.root / "smd" / PROJECT / "data" / "verified-projects.json"
        evidence.parent.mkdir(parents=True)
        evidence.write_text(json.dumps(claim()["projects"]))
        subprocess.run(["bash"], input=script, env=environment, check=True, timeout=10)
        receipt = json.loads(captured.read_text())
        self.assertEqual(receipt["instance_id"], INSTANCE)
        self.assertEqual(receipt["revision"], REVISION)
        self.assertEqual(receipt["project_id"], PROJECT)
        public = self.root / "public.der"
        public.write_bytes(bytes.fromhex("302a300506032b6570032100") + base64.b64decode(key["public_key"]))
        signature = self.root / "signature"
        signature.write_bytes(base64.b64decode(receipt["signature"]))
        message = self.root / "message"
        expected = {"portal_access": "owner-pkce-v1", "ipv4": IPV4, "ssh_user": "root", "portal_url": f"https://{PORTAL}",
                    "coder_url": f"https://{CODER}",
                    "apps": [{"project_id": PROJECT, "revision": APP_REVISION, "url": f"https://{APP}"}]}
        self.assertEqual(receipt["connections"], expected)
        captured.unlink()
        invalid_portal = subprocess.run(["bash"], input=script, env={**environment,"TEST_PORTAL_METADATA":"{}"},
                                        capture_output=True, timeout=10)
        self.assertNotEqual(invalid_portal.returncode, 0)
        self.assertFalse(captured.exists())
        install_only = claim()
        install_only["projects"][0]["verify_installation"] = True
        evidence.write_text(json.dumps(install_only["projects"]))
        subprocess.run(["bash"], input=bootstrap(install_only), env={**environment, "TEST_INSTALL_MARKER": APP_REVISION,
                       "TEST_UNCONFIGURED_APP": f"https://{APP}/"}, check=True, timeout=10)
        self.assertEqual(json.loads(captured.read_text())["connections"]["apps"][0]["runtime_status"], "runtime_unverified")
        evidence.write_text(json.dumps(claim()["projects"]))
        canonical = json.dumps(expected, sort_keys=True, separators=(",", ":"))
        message.write_text(f"{TASK}\n{PROJECT}\n{INSTANCE}\n{REVISION}\n{canonical}\n")
        subprocess.run(["openssl", "pkeyutl", "-verify", "-pubin", "-rawin", "-keyform", "DER",
                        "-inkey", public, "-sigfile", signature, "-in", message],
                       capture_output=True, check=True)
        git_request = json.loads((self.root / "git-request").read_text())
        self.assertEqual(git_request["project_id"], PROJECT)
        self.assertEqual(git_request["instance_id"], INSTANCE)
        self.assertRegex(git_request["nonce"], r"^[0-9a-f]{64}$")
        signature.write_bytes(base64.b64decode(git_request["signature"]))
        message.write_text(f"git-token\n{TASK}\n{PROJECT}\n{INSTANCE}\n"
                           f"{git_request['timestamp']}\n{git_request['nonce']}\n")
        subprocess.run(["openssl", "pkeyutl", "-verify", "-pubin", "-rawin", "-keyform", "DER",
                        "-inkey", public, "-sigfile", signature, "-in", message],
                       capture_output=True, check=True)
        self.assertFalse((self.root / "smd-git" / PROJECT).exists())
        subprocess.run(["bash"], input=script, env={**environment, "TEST_GIT_BUNDLE": "1"},
                       check=True, timeout=10)
        staged = evidence.parent / "git-bundles"
        self.assertEqual((staged / f"{PROJECT}.bundle").read_bytes(), b"fixture-bundle")
        self.assertEqual(list(staged.glob(".incoming-*")), [])
        self.assertFalse((self.root / "smd-git" / PROJECT).exists())
        prepared = claim()
        prepared["projects"][0]["build_context_sha256"] = sha256(b"fixture-context").hexdigest()
        bundle_env = {**environment, "TEST_GIT_BUNDLE": "1"}
        captured.unlink()
        old_evidence = subprocess.run(["bash"], input=bootstrap(prepared), env=bundle_env, capture_output=True, timeout=10)
        self.assertNotEqual(old_evidence.returncode, 0)
        self.assertFalse(captured.exists())
        evidence.write_text(json.dumps(prepared["projects"]))
        subprocess.run(["bash"], input=bootstrap(prepared), env=bundle_env, check=True, timeout=10)
        delivered = staged / f"{PROJECT}.tar.gz"
        self.assertEqual(delivered.read_bytes(), b"fixture-context")
        context_request = json.loads((self.root / "git-request").read_text())
        self.assertEqual(context_request["artifact"], "build-context")
        self.assertNotEqual(context_request["nonce"], git_request["nonce"])
        signature.write_bytes(base64.b64decode(context_request["signature"]))
        message.write_text(f"build-context\n{TASK}\n{PROJECT}\n{INSTANCE}\n"
                           f"{context_request['timestamp']}\n{context_request['nonce']}\n")
        subprocess.run(["openssl", "pkeyutl", "-verify", "-pubin", "-rawin", "-keyform", "DER",
                        "-inkey", public, "-sigfile", signature, "-in", message], capture_output=True, check=True)
        prepared["projects"][0]["qa_runtime_versions"] = {"LICENSE_KEY": 3}
        with_runtime = bootstrap(prepared)
        self.assertNotIn(b"fixture-runtime", with_runtime)
        subprocess.run(["bash"], input=with_runtime, env=bundle_env, check=True, timeout=10)
        runtime_path = evidence.parent / "project-runtime" / f"{PROJECT}.env"
        self.assertEqual(runtime_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(runtime_path.read_bytes(), b"LICENSE_KEY=fixture-runtime\n")
        prepared["projects"][0]["build_context_sha256"] = "0" * 64
        delivered.unlink()
        captured.unlink()
        invalid = subprocess.run(["bash"], input=bootstrap(prepared), env=bundle_env, capture_output=True, timeout=10)
        self.assertNotEqual(invalid.returncode, 0)
        self.assertFalse(delivered.exists())
        self.assertFalse(captured.exists())
        installer.write_bytes(b"#!/bin/bash\nprintf 'unexpected\\n'\n")
        failed = subprocess.run(["bash"], input=script, env=environment, capture_output=True, timeout=10)
        self.assertNotEqual(failed.returncode, 0)
        self.assertFalse(captured.exists())


if __name__ == "__main__":
    unittest.main()
