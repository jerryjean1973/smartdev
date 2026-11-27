"""Closed set of release inputs shared by the package builder and download handler."""
import hashlib
import io
import json
import os
from pathlib import Path
import re
import tarfile
from functools import lru_cache

RELEASE_FILES = frozenset(("apps.py","architect.py","architect-compose.yaml","compose.yaml","configure.py",
    "configure.sh","enroll-api.py","enrollment-compose.yaml","enrollment.py","managed_workspace.py",
    "openbao.hcl","projects.py","vault-bootstrap.py","vault-policy.hcl","workspace_tools.py"))
TEMPLATE = frozenset(("main.tf","start.sh",".terraform.lock.hcl"))
TOOLS = frozenset(("github_known_hosts","service.py","health.py","accounts.py","chromium.tar.gz","chromium-seccomp.json",
    "smartdev-workspace-health.service","smartdev-workspace-health.timer","smartdev-workspace-tools.service",
    "smartdev-workspace-accounts.service","smartdev-workspace-accounts.timer"))
CLIENTS = frozenset(("workspace-browser-start","workspace-screenshot","workspace-dev-login","command-dev-login.cjs",
    "git-remote-smartdev","workspace-git-credential","codex-governance-hook","workspace-tools-check","workspace_git.py","git-mcp.toml"))
EXTRAS = {"revision","images.env","README.md","INSTALL-OFFLINE.md","images.tar.gz","managed-dns.json","configure-managed-dns.py"}
MODEL = "models/manifests/registry.ollama.ai/library/qwen2.5/1.5b"
ALLOWED = RELEASE_FILES | EXTRAS | {"coder-template/"+name for name in TEMPLATE | {"seccomp.json","LICENSE.moby-profiles","command-codex-workspace"}} | {"workspace-tools/"+name for name in TOOLS} | {"workspace-tools/clients/"+name for name in CLIENTS} | {MODEL}


def policy(zone):
    if not re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}",zone):
        raise ValueError("Invalid managed DNS zone")
    return {"schema":1,"zone":zone,"portal":"smd-{deployment}."+zone,"coder":"coder-{deployment}."+zone,
            "app":"app-{project12}-{deployment12}."+zone,"dev":"dev-{project12}-{deployment12}."+zone,"preview_default":"dev","environments":["DEV","QA","PREPROD","PROD"],
            "custom_domain":"Verify ownership and application HTTPS before switching environments.",
            "configuration":"Fresh installation credentials. No owner configuration bindings in the archive."}


def additions(zone):
    return {"managed-dns.json":(json.dumps(policy(zone),indent=2)+"\n").encode(),
            "configure-managed-dns.py":Path(__file__).with_name("smd_configure_dns.py").read_bytes()}


def allowed_members(archive):
    found={}
    for member in archive:
        name=member.name
        if not member.isfile() or name in found or (name not in ALLOWED and not re.fullmatch(r"models/blobs/sha256-[a-f0-9]{64}",name)):
            raise ValueError("Unexpected file in the offline release")
        found[name]=member
    if not {"compose.yaml","configure.sh","revision","images.tar.gz","images.env"}<=found.keys():
        raise ValueError("Incomplete offline release")
    images=found["images.env"]
    if images.size>8192:
        raise ValueError("Invalid image references")
    references=archive.extractfile(images).read().decode("ascii").splitlines()
    if not references or not all(re.fullmatch(r"SMD_[A-Z_]+_IMAGE=sha256:[0-9a-f]{64}",line) for line in references):
        raise ValueError("Unpinned offline images")
    return found


def clean_proof(path,expected):
    if path.is_symlink() or not path.is_file() or path.stat().st_size>4096:
        raise ValueError("Offline release requires an image audit")
    raw=json.loads(path.read_text())
    counts={key:raw[key] for key in ("images","layers","files_checked")}
    if (raw.get("base_sha256")!=expected or raw.get("schema")!=1
            or not all(type(v) is int and v>0 for v in counts.values())):
        raise ValueError("Offline image audit is invalid")
    return {"schema":1,"base_sha256":expected,**counts,
            "scope":"Pinned release and image configurations, credential-bearing image paths and baked credential environment variables."}


def file_identity(stat):
    return (stat.st_dev,stat.st_ino,stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns)


def stream_range(parts,source,start,end):
    offset=0
    for part in parts:
        length=len(part) if isinstance(part,bytes) else part[1]
        left,right=max(0,start-offset),min(length,end-offset+1)
        if right>left:
            if isinstance(part,bytes):
                yield part[left:right]
            else:
                source.seek(part[0]+left)
                remaining=right-left
                while remaining:
                    data=source.read(min(1024*1024,remaining))
                    if not data:
                        raise OSError("Offline release truncated")
                    yield data
                    remaining-=len(data)
        offset+=length
        if offset>end: break


@lru_cache(maxsize=4)
def verified_layout(path,identity,expected,zone,proof_json):
    with open(path,"rb") as source,tarfile.open(fileobj=source,mode="r:") as archive:
        source.seek(0)
        if hashlib.file_digest(source,"sha256").hexdigest()!=expected:
            raise ValueError("Offline release checksum mismatch")
        source.seek(archive.offset)
        found=allowed_members(archive)
        extra=additions(zone)
        extra["export-attestation.json"]=(proof_json+"\n").encode()
        extra["configure.sh"]=b'#!/bin/sh\nexec python3 ./configure-managed-dns.py -i "$@"\n'
        with tarfile.open(Path(__file__).with_name("smd_workbench.tar.gz")) as workbench:
            extra["enroll-api.py"]=workbench.extractfile("enroll-api.py").read()
        extra["INSTALL-OFFLINE.md"]=archive.extractfile(found["INSTALL-OFFLINE.md"]).read()+b"""
The first configure.sh run generates unique managed portal, Coder, application and DEV
hostnames. The DNS request is saved in the project's data/managed-dns.json.
An offline or self-hosted installation must register those records with the DNS
owner for its publicly reachable HTTPS ingress. Hosted SMD deployments do this
automatically. Customer domain cutover requires ownership and HTTPS verification.
This copy starts with its own local credentials. It carries no owner binding.
"""
        prefix=io.BytesIO()
        ranges=[]
        def add(name,body,mode=0o644):
            info=tarfile.TarInfo(name)
            info.mode=mode
            info.size=len(body)
            prefix.write(info.tobuf(tarfile.USTAR_FORMAT))
            prefix.write(body)
            prefix.write(b"\0"*(-len(body)%512))
        for name,body in extra.items():
            add(name,body,0o755 if name=="configure.sh" else 0o644)
        for name,item in found.items():
            if name in extra: continue
            if name=="images.tar.gz" or name.startswith("models/blobs/") or item.size>256*1024:
                end=item.offset_data+((item.size+511)//512)*512
                ranges.append((item.offset,end-item.offset))
            else:
                add(name,archive.extractfile(item).read(),item.mode&0o755)
        parts=[prefix.getvalue(),*ranges]
        size=len(parts[0])+sum(span for _,span in ranges)
        padding=1024+(-(size+1024)%10240)
        parts.append(b"\0"*padding)
        size+=padding
        digest=hashlib.sha256()
        for part in stream_range(parts,source,0,size-1): digest.update(part)
        if file_identity(os.fstat(source.fileno()))!=identity:
            raise ValueError("Release changed during audit")
        return parts,digest.hexdigest(),size
