"""Install customer HTTPS aliases on the selected SMD instance using Caddy's ACME."""
from pathlib import Path
import json
import re
import subprocess
import sys
from uuid import UUID

BEGIN = "# BEGIN SMD CUSTOMER DOMAINS"
END = "# END SMD CUSTOMER DOMAINS"


def render(prior, aliases):
    if prior.count(BEGIN)!=prior.count(END) or prior.count(BEGIN)>1:
        raise ValueError("Ambiguous managed customer domain section")
    if BEGIN in prior:
        head,block=prior.split(BEGIN,1)
        _,tail=block.split(END,1)
        base=head.rstrip()+"\n"+tail.lstrip("\n")
    else:
        base=prior
    blocks=[]
    for alias,managed in sorted(aliases.items()):
        if not re.fullmatch(r"(?:[a-z0-9-]+\.)+[a-z]{2,63}",alias) or alias==managed:
            raise ValueError("Invalid customer hostname")
        route=re.search(r"(?ms)^"+re.escape(managed)+r":443(?:, [a-z0-9.-]+:443)? \{\n(.*?)^\}",base)
        if not route or re.search(r"(?m)^"+re.escape(alias)+r"(?::443)?\s*\{",base):
            raise ValueError("Unsafe or missing application route")
        body=re.sub(r"(?m)^\s*tls [^\n]+\n","",route[1])
        if "reverse_proxy " not in body:
            raise ValueError("Missing application upstream")
        blocks.append(f"{alias} {{\n"+body+"}\n")
    if not blocks:
        return base
    return base.rstrip()+"\n\n"+BEGIN+"\n"+"".join(blocks)+END+"\n"


def execute(project):
    project=str(UUID(project))
    data=Path("/opt/smartdev")/project/"data"
    def run(*args, **kwargs):
        return subprocess.check_output(args,text=True,timeout=60,**kwargs).strip()
    def service(name):
        found=run("docker","ps","-q","--filter","label=com.docker.compose.project=smd-"+project,
                  "--filter","label=com.docker.compose.service="+name)
        if not re.fullmatch("[a-f0-9]{12,64}",found):
            raise ValueError("Missing or ambiguous deployment service")
        return found
    api,edge=service("api"),service("edge")
    rows=json.loads(run("docker","exec",api,"python","-c",
        "from smd_preview_domains import eligible_aliases; import json; print(json.dumps(eligible_aliases()))"))
    for name in ("Caddyfile-enrolled","Caddyfile"):
        path=data/name
        if not path.is_file():
            continue
        old=path.read_text()
        updated=render(old,rows)
        if old==updated:
            continue
        candidate=data/(name+".customer-candidate")
        candidate.write_text(updated)
        remote="/tmp/smd-custom-domains.Caddyfile"
        run("docker","cp",str(candidate),edge+":"+remote)
        run("docker","exec",edge,"caddy","validate","--adapter","caddyfile","--config",remote)
        if path.read_text()!=old:
            raise ValueError("Ingress changed during verification")
        if name=="Caddyfile":
            run("docker","exec",edge,"caddy","reload","--adapter","caddyfile","--config",remote)
        path.write_text(updated)
        candidate.unlink()


if __name__=="__main__":
    execute(sys.argv[1])
