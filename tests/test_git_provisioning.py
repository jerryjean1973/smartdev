"""Exercise the shared provisioner through native Git and local transport fixtures."""
import importlib.machinery
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SOURCE=Path(__file__).resolve().parents[1]/"workspace"/"git-sync"
sync=importlib.machinery.SourceFileLoader("smartdev_git_sync",str(SOURCE)).load_module()


class GitProvisioningTest(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.home=self.root/"home"
        self.home.mkdir()
        environment=patch.dict(os.environ,{"HOME":str(self.home),"GIT_CONFIG_NOSYSTEM":"1",
            "GIT_AUTHOR_NAME":"SmartDev test","GIT_AUTHOR_EMAIL":"test@example.invalid",
            "GIT_COMMITTER_NAME":"SmartDev test","GIT_COMMITTER_EMAIL":"test@example.invalid"})
        environment.start()
        self.addCleanup(environment.stop)
        original=sync.git
        transport=patch.object(sync,"git",side_effect=lambda cwd,*args,**kwargs:
                               original(cwd,"-c","protocol.file.allow=always",*args,**kwargs))
        self.native=transport.start()
        self.addCleanup(transport.stop)

    def git(self,directory,*args):
        return subprocess.run(["git",*args],cwd=directory,check=True,capture_output=True,text=True).stdout.strip()

    def repository(self,name):
        directory=self.root/name
        directory.mkdir()
        self.git(directory,"init","--initial-branch=main")
        (directory/"app.txt").write_text(name+"\n")
        self.git(directory,"add","app.txt")
        self.git(directory,"commit","-m","Fixture")
        url="https://github.com/test/"+name+".git"
        self.git(directory,"config","--global","url."+str(directory)+".insteadOf",url)
        return directory,url

    def module(self,parent,child,url,relative):
        self.git(parent,"-c","protocol.file.allow=always","submodule","add","--",url,relative)
        self.git(parent,"commit","-m","Add pinned module")

    def clean_clone(self,remote):
        directory=self.root/"checkout"
        self.git(self.root,"-c","protocol.file.allow=always","clone","--",remote,str(directory))
        return directory

    def test_nested_and_repeated_provisioning_preserves_parent_and_pins(self):
        leaf,leaf_url=self.repository("leaf")
        child,child_url=self.repository("child")
        main,main_url=self.repository("main")
        self.module(child,leaf,leaf_url,"nested/leaf")
        self.module(main,child,child_url,"applications/child")
        checkout=self.clean_clone(main_url)
        pin=self.git(checkout,"rev-parse","HEAD")
        for _ in range(2):
            issues=[]
            sync.provision_modules(checkout,"test/main",issues)
            self.assertEqual(issues,[])
            self.assertEqual(self.git(checkout,"rev-parse","HEAD"),pin)
            self.assertEqual(self.git(checkout,"branch","--show-current"),"main")
            self.assertEqual(self.git(checkout/"applications/child/nested/leaf","rev-parse","HEAD"),self.git(leaf,"rev-parse","HEAD"))

    def test_reuses_unregistered_populated_git_checkout(self):
        child,child_url=self.repository("child")
        main,main_url=self.repository("main")
        self.module(main,child,child_url,"application")
        checkout=self.clean_clone(main_url)
        self.git(checkout,"-c","protocol.file.allow=always","clone","--",child_url,"application")
        populated=checkout/"application/app.txt"
        populated.write_text("Local edits stay intact.")
        self.assertTrue(self.git(checkout,"submodule","status").startswith("-"))
        issues=[]
        sync.provision_modules(checkout,"test/main",issues)
        self.assertEqual(issues,[])
        self.assertFalse(self.git(checkout,"submodule","status").startswith("-"))
        self.assertEqual(populated.read_text(),"Local edits stay intact.")
        self.assertEqual(self.git(checkout/"application","branch","--show-current"),"main")

    def test_preserves_non_git_files_in_incomplete_submodule(self):
        child,child_url=self.repository("child")
        main,main_url=self.repository("main")
        self.module(main,child,child_url,"application")
        checkout=self.clean_clone(main_url)
        existing=checkout/"application/untracked.txt"
        existing.write_text("Keep me.")
        issues=[]
        sync.provision_modules(checkout,"test/main",issues)
        self.assertEqual(issues[0]["kind"],"workspace_conflict")
        self.assertEqual(existing.read_text(),"Keep me.")

    def test_registered_submodule_at_other_commit_is_preserved(self):
        child,child_url=self.repository("child")
        main,main_url=self.repository("main")
        self.module(main,child,child_url,"application")
        checkout=self.clean_clone(main_url)
        sync.provision_modules(checkout,"test/main",[])
        submodule=checkout/"application"
        self.git(submodule,"switch","-c","team")
        data=submodule/"local.txt"
        data.write_text("New local commit")
        self.git(submodule,"add","local.txt")
        self.git(submodule,"commit","-m","New local commit")
        prior=self.git(submodule,"rev-parse","HEAD")
        issues=[]
        sync.provision_modules(checkout,"test/main",issues)
        self.assertEqual(issues[0]["kind"],"workspace_conflict")
        self.assertEqual(self.git(submodule,"rev-parse","HEAD"),prior)
        self.assertEqual(self.git(submodule,"branch","--show-current"),"team")

    def test_rejects_invalid_submodule_url_before_network(self):
        child,child_url=self.repository("child")
        main,main_url=self.repository("main")
        self.module(main,child,child_url,"application")
        checkout=self.clean_clone(main_url)
        self.git(checkout,"config","--file",".gitmodules","submodule.application.url","file:///private/repo")
        self.git(checkout,"add",".gitmodules")
        self.git(checkout,"commit","-m","Invalid URL fixture")
        issues=[]
        sync.provision_modules(checkout,"test/main",issues)
        self.assertEqual(issues[0]["kind"],"invalid_submodule_url")
        self.assertIsNone(sync.checkout_head(checkout/"application"))

    def test_permission_auth_and_pin_failures_are_distinguished_and_redacted(self):
        for stderr,kind in [("Authentication failed","authentication_rejected"),
                            ("Repository not found","permission_denied"),
                            ("not our ref","pin_unavailable"),
                            ("Could not resolve host","network_timeout")]:
            issues=[]
            result=subprocess.CompletedProcess([],1,"",stderr+" https://another:password123@github.com/test/child")
            sync.failure(issues,result,"Fetch pinned submodule","test/child","application")
            self.assertEqual(issues[0]["kind"],kind)
            self.assertEqual(issues[0]["repository"],"test/child")
            self.assertNotIn("password123",issues[0]["stderr"])

    def test_relative_github_url_and_ssh_identity(self):
        self.assertEqual(sync.resolve_identity("../shared.git","example/parent"),"example/shared")
        self.assertEqual(sync.identity("git@github.com:example/shared.git"),"example/shared")
        self.assertEqual(sync.identity("ssh://git@github.com/example/shared.git"),"example/shared")
        self.assertIsNone(sync.identity("https://token@github.com/example/shared.git"))

    def test_no_submodules_is_successful(self):
        repository,_=self.repository("only")
        issues=[]
        sync.provision_modules(repository,"test/only",issues)
        self.assertEqual(issues,[])

    def test_resumes_an_empty_interrupted_submodule_workspace(self):
        child,child_url=self.repository("child")
        main,main_url=self.repository("main")
        self.module(main,child,child_url,"application")
        checkout=self.clean_clone(main_url)
        self.git(checkout/"application","init","--initial-branch=main")
        issues=[]
        sync.provision_modules(checkout,"test/main",issues)
        self.assertEqual(issues,[])
        self.assertEqual(self.git(checkout/"application","rev-parse","HEAD"),self.git(child,"rev-parse","HEAD"))

    def test_two_concurrent_requests_share_checkout_lock_and_recent_result(self):
        from concurrent.futures import ThreadPoolExecutor
        import json
        import sys

        _,url=self.repository("source")
        project=self.root/"p-fixture"
        project.mkdir()
        self.git(project,"-c","protocol.file.allow=always","clone","--",url,"repository")
        with patch.dict(os.environ,{"PROJECT_NAME":"p-fixture"}), patch.object(sync,"REPOSITORY","test/source"), \
             patch.object(sys,"argv",["git-sync","--recent"]), patch.object(sync,"reconcile",wraps=sync.reconcile) as reconcile:
            with ThreadPoolExecutor(max_workers=2) as workers:
                calls=list(workers.map(lambda _:sync.main(self.root),range(2)))
        self.assertEqual(calls,[0,0])
        self.assertEqual(reconcile.call_count,1)
        self.assertTrue(json.loads((project/"config/git-sync.json").read_text())["submodulesComplete"])
