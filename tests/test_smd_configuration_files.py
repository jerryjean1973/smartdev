import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/"api"),str(ROOT/"scripts")]
from smd_custom_ingress import render
from smd_export_policy import allowed_members,clean_proof,policy,stream_range
from smd_configure_dns import hosts


class ConfigurationFiles(unittest.TestCase):
    def test_customer_route_and_rollback_preserve_other_sites(self):
        base='app-1.example:443 {\n    tls /ca/public /ca/key\n    reverse_proxy app:8090\n}\nother.example {\n respond 200\n}\n'
        changed=render(base,{"dev.customer.example":"app-1.example"})
        self.assertIn("dev.customer.example {\n    reverse_proxy app:8090",changed)
        self.assertEqual(render(changed,{"dev.customer.example":"app-1.example"}),changed)
        tail='later.example {\n respond 200\n}\n'
        self.assertEqual(render(changed+tail,{}),base+tail)

    def test_unique_default_hosts(self):
        project,other,deployment,second=[uuid4() for _ in range(4)]
        template=policy("smd.youkandoit.com")
        one,two,three=[hosts(p,d,template) for p,d in ((project,deployment),(other,deployment),(project,second))]
        self.assertEqual(one["portal"],two["portal"])
        self.assertEqual(len({one["dev"],one["app"],two["dev"],three["dev"]}),4)
        self.assertTrue(all(s.endswith(".smd.youkandoit.com") for s in one.values()))

    def test_reject_runtime_material_even_in_an_otherwise_valid_tar(self):
        out=io.BytesIO()
        with tarfile.open(fileobj=out,mode="w") as package:
            for name in ("revision","configure.sh","compose.yaml","images.tar.gz","images.env",".env"):
                data=("SMD_API_IMAGE=sha256:"+"a"*64 if name=="images.env" else "fixture").encode()
                m=tarfile.TarInfo(name);m.size=len(data);package.addfile(m,io.BytesIO(data))
        out.seek(0)
        with tarfile.open(fileobj=out) as package,self.assertRaises(ValueError):
            allowed_members(package)

    def test_partial_download_crosses_generated_and_pinned_content(self):
        parts=[b"0123",(2,4),b"89"]
        for a in range(10):
            for b in range(a,10):
                self.assertEqual(b"".join(stream_range(parts,io.BytesIO(b"xx4567yy"),a,b)),b"0123456789"[a:b+1])

    def test_attestation_cannot_be_reused_on_another_release(self):
        with tempfile.TemporaryDirectory() as d:
            proof=Path(d)/"proof"
            proof.write_text(json.dumps(dict(schema=1,base_sha256="a"*64,images=1,layers=1,files_checked=1)))
            with self.assertRaises(ValueError):
                clean_proof(proof,"b"*64)


if __name__=="__main__":
    unittest.main()
