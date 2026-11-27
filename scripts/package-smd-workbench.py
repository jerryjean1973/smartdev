"""Pack a compiled Next portal and verified installer helpers for managed workspaces."""
import gzip
import io
from pathlib import Path
import sys
import tarfile

def tar(files):
    stream=io.BytesIO()
    with gzip.GzipFile(fileobj=stream,mode="wb",mtime=0) as gz, tarfile.open(fileobj=gz,mode="w|") as out:
        for name,content in sorted(files):
            info=tarfile.TarInfo(name)
            info.size=len(content)
            info.mode=0o644
            out.addfile(info,io.BytesIO(content))
    return stream.getvalue()

def package(build,root):
    if not (build/"BUILD_ID").is_file():
        raise ValueError("Expected a compiled Next portal")
    compiled=[]
    for entry in build.rglob("*"):
        name=entry.relative_to(build)
        if name.parts[0] not in {"cache","diagnostics","trace","types"} and entry.is_file() and not entry.is_symlink():
            compiled.append((".next/"+name.as_posix(),entry.read_bytes()))
    scripts=root/"release"/"smd"
    files=[(name,(scripts/name).read_bytes()) for name in ("managed_workspace.py","enroll-api.py","apps.py")]
    (root/"api"/"smd_workbench.tar.gz").write_bytes(tar([*files,("next.tar.gz",tar(compiled))]))

if __name__=="__main__":
    package(Path(sys.argv[1]),Path(__file__).resolve().parents[1])
