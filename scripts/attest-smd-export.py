"""Audit immutable offline image layers before the owner API publishes a clean export."""
import argparse
import hashlib
import json
from pathlib import Path,PurePosixPath
import re
import sys
import tarfile
import tempfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"api"))
from smd_export_policy import allowed_members

SENSITIVE_ENV=re.compile(r"(?:PASSWORD|API_?KEY|AUTH_TOKEN|CLIENT_SECRET|PRIVATE_KEY|SECRET_ACCESS_KEY|SESSION_KEY)$",re.I)
PRIVATE_PATHS=("run/smd-generations/","run/smd-install-identity","run/command-vault/token","vault-operator/recovery",
               "root/.aws/credentials","root/.docker/config.json","root/.config/gh/hosts.yml",
               "root/.codex/auth.json","opt/smartdev/","vault-client/token")
DEPLOYED_ENV=re.compile(r"^(?:(?:app|workspace|projects|opt/app|home/[^/]+|root)(?:/[^/]+){0,3}/)?(?:\.env(?:\.(?:local|production|dev|qa))?|secrets\.env|owner-password|installation\.pem)$")


def verify_layer(stream):
    files=0
    with tarfile.open(fileobj=stream,mode="r|gz",bufsize=256*1024) as layer:
        for member in layer:
            path=PurePosixPath(member.name).as_posix().lstrip("/")
            if not member.isfile():
                continue
            files+=1
            if (path.startswith(PRIVATE_PATHS) or DEPLOYED_ENV.fullmatch(path) or
                re.search(r"(?:^|/)\.ssh/id_(?:rsa|ed25519|ecdsa|dsa)$",path)):
                raise ValueError("Runtime credential material found in an image layer: "+path)
    return files


class Replay:
    def __init__(self,prefix,stream):
        self.prefix,self.stream=prefix,stream
    def read(self,size=-1):
        data=self.prefix[:size] if size>=0 else self.prefix
        self.prefix=self.prefix[len(data):]
        return data+self.stream.read(size-len(data) if size>=0 else -1)


def audit(path,expected):
    with path.open("rb") as file:
        if hashlib.file_digest(file,"sha256").hexdigest()!=expected:
            raise ValueError("Release digest mismatch")
        file.seek(0)
        with tarfile.open(fileobj=file) as release:
            files=allowed_members(release)
            pins={v.split("=sha256:")[1] for v in release.extractfile(files["images.env"]).read().decode().splitlines()}
            configs=set()
            graph={}
            layers=members=0
            with tarfile.open(fileobj=release.extractfile(files["images.tar.gz"]),mode="r|gz",bufsize=256*1024) as images:
                for item in images:
                    if not item.isfile(): continue
                    body=images.extractfile(item)
                    if item.name.startswith("blobs/sha256/"):
                        prefix=body.read(2)
                        if prefix==b"\x1f\x8b":
                            members+=verify_layer(Replay(prefix,body))
                            layers+=1
                        elif item.size<=8*1024*1024:
                            obj=json.loads(prefix+body.read())
                            key=item.name.split("/")[-1]
                            if "manifests" in obj:
                                graph[key]=[v["digest"].removeprefix("sha256:") for v in obj["manifests"]]
                            elif obj.get("schemaVersion")==2 and "config" in obj:
                                graph[key]=[obj["config"]["digest"].removeprefix("sha256:")]
                            elif "rootfs" in obj and "config" in obj:
                                config=obj["config"]
                                for setting in config.get("Env",[]):
                                    name,_,value=setting.partition("=")
                                    if value and SENSITIVE_ENV.search(name):
                                        raise ValueError("Baked credential in image environment: "+name)
                                configs.add(item.name.split("/")[-1])
                        else:
                            raise ValueError("Unrecognized image blob")
            def includes_config(digest,seen):
                if digest in configs: return True
                if digest in seen: return False
                return any(includes_config(child,seen|{digest}) for child in graph.get(digest,[]))
            if not all(includes_config(pin,set()) for pin in pins) or not layers or not members:
                raise ValueError("Incomplete image provenance")
    return {"schema":1,"base_sha256":expected,"images":len(pins),"layers":layers,"files_checked":members,
            "checks":["pinned release digest","closed release file list","pinned image configurations","credential-bearing image paths","baked credential environment variables"]}


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive",type=Path)
    parser.add_argument("sha256")
    parser.add_argument("output",type=Path)
    args=parser.parse_args()
    result=audit(args.archive,args.sha256)
    with tempfile.NamedTemporaryFile(mode="w",dir=args.output.parent,delete=False) as f:
        json.dump(result,f,sort_keys=True)
        name=Path(f.name)
    name.chmod(0o644)
    name.replace(args.output)
    print(f"Audited {result['images']} offline images and {result['layers']} layers")
