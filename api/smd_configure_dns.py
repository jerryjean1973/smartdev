"""Set the managed HTTPS origins when an offline SMD copy is configured."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from uuid import UUID,uuid4


def hosts(project,deployment,policy):
    args={"project12":project.hex[:12],"deployment12":deployment.hex[:12],"deployment":deployment.hex}
    return {name:policy[name].format(**args) for name in ("portal","coder","app","dev")}


def install(project,manifest,policy):
    root=Path.cwd().parent.parent
    if root.name!=str(project):
        raise ValueError("Configure the release inside its project directory")
    data=root/"data"
    data.mkdir(mode=0o700,exist_ok=True)
    identity=data/"offline-deployment-id"
    if (data/"public-host").exists() and not identity.exists():
        # The managed runner has already supplied the authoritative hosts, or an
        # operator has explicitly configured customer DNS for this existing installation.
        subprocess.run([sys.executable,"configure.py",str(project),*([str(manifest)] if manifest else [])],check=True)
        return
    try:
        with identity.open("x") as f:
            f.write(str(uuid4())+"\n")
    except FileExistsError:
        pass
    deployment=UUID(identity.read_text().strip())
    base=hosts(project,deployment,policy)
    for name,role in (("public-host","portal"),("coder-host","coder")):
        path=data/name
        if not path.exists():
            path.write_text(base[role]+"\n")
    spec=json.loads(manifest.read_text()) if manifest else {}
    records={"zone":policy["zone"],"deployment":str(deployment),"portal":base["portal"],"coder":base["coder"],"projects":[]}
    for item in spec.get("projects",[]):
        assigned=hosts(UUID(item["id"]),deployment,policy)
        item["hostname"]=assigned["app"]
        item["preview_hostname"]=assigned["dev"]
        records["projects"].append({"id":item["id"],"app":assigned["app"],"dev":assigned["dev"]})
    (data/"managed-dns.json").write_text(json.dumps(records,indent=2)+"\n")
    if manifest:
        with tempfile.NamedTemporaryFile(mode="w",dir=data) as staged:
            json.dump(spec,staged);staged.flush()
            subprocess.run([sys.executable,"configure.py",str(project),staged.name],check=True)
    else:
        subprocess.run([sys.executable,"configure.py",str(project)],check=True)
    verified=data/"verified-projects.json"
    if verified.is_file():
        entries=json.loads(verified.read_text())
        previews={p["id"]:p["dev"] for p in records["projects"]}
        for item in entries:
            if item["id"] in previews:
                item["preview_hostname"]=previews[item["id"]]
        verified.write_text(json.dumps(entries))
    for filename in ("Caddyfile","Caddyfile-enrolled"):
        file=data/filename
        if file.is_file():
            config=file.read_text()
            for item in records["projects"]:
                config=config.replace(item["app"]+":443 {",item["app"]+":443, "+item["dev"]+":443 {")
            file.write_text(config)


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-i",action="store_true",help="Initialize the local SMD installation")
    parser.add_argument("project",type=UUID)
    parser.add_argument("manifest",type=Path,nargs="?")
    parser.add_argument("--deployment",type=UUID,help="Show the names for an existing enrollment")
    args=parser.parse_args()
    policy=json.loads(Path(__file__).with_name("managed-dns.json").read_text())
    if args.i:
        install(args.project,args.manifest,policy)
    elif args.deployment:
        print(json.dumps(hosts(args.project,args.deployment,policy),indent=2))
    else:
        parser.error("Pass -i to configure or --deployment to show assigned hosts")
