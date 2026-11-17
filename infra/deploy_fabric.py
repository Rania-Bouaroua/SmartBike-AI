"""Deploy the SmartBike AI Fabric items with the Fabric REST API (idempotent: existing items are reused).

Creates or updates, in an existing workspace:
  1. Lakehouse            lh_smartbike
  2. Environment          env_smartbike  + custom library dist/smartbike-<v>-py3-none-any.whl  (+ publish)
  3. Notebooks            nb_01 ... nb_05  from notebooks/fabric/*.ipynb (regenerated from the .py sources)

    pip install azure-identity requests
    python infra/deploy_fabric.py --workspace-id <guid>                 # dry run: prints the plan, calls nothing
    python infra/deploy_fabric.py --workspace-id <guid> --apply         # signs in (browser / az login) and deploys

Not automated (portal, a few clicks - docs/reproducibility.md): attaching the Lakehouse and the Environment as
defaults of each notebook, the Eventstream / Eventhouse, the pipeline schedule, the Power BI report.
Status: written against the public Fabric REST API (v1); not yet run against a real tenant by the authors.
"""
from __future__ import annotations

import argparse
import base64
import pathlib
import sys
import time

import requests

API = "https://api.fabric.microsoft.com/v1"
SCOPE = "https://api.fabric.microsoft.com/.default"
ROOT = pathlib.Path(__file__).resolve().parents[1]


class Fabric:
    def __init__(self, workspace_id: str, apply: bool):
        self.ws, self.apply = workspace_id, apply
        self.session = requests.Session()
        if apply:
            from azure.identity import AzureCliCredential, ChainedTokenCredential, InteractiveBrowserCredential
            cred = ChainedTokenCredential(AzureCliCredential(), InteractiveBrowserCredential())
            self.session.headers["Authorization"] = f"Bearer {cred.get_token(SCOPE).token}"

    def call(self, method: str, path: str, **kw) -> dict:
        url = f"{API}/workspaces/{self.ws}{path}"
        if not self.apply:
            print(f"  [dry-run] {method} {url}")
            return {"id": "<dry-run>", "value": []}
        r = self.session.request(method, url, timeout=120, **kw)
        if r.status_code == 202 and "Location" in r.headers:            # long-running operation
            return self._wait(r.headers["Location"], int(r.headers.get("Retry-After", 5)))
        if r.status_code >= 400:
            raise RuntimeError(f"{method} {path} -> {r.status_code}: {r.text[:500]}")
        return r.json() if r.content else {}

    def _wait(self, location: str, retry: int) -> dict:
        for _ in range(120):
            time.sleep(retry)
            r = self.session.get(location, timeout=60)
            state = r.json().get("status", "")
            if state in ("Succeeded", "Completed"):
                res = self.session.get(location + "/result", timeout=60)
                return res.json() if res.ok and res.content else {}
            if state in ("Failed", "Cancelled"):
                raise RuntimeError(f"operation failed: {r.text[:500]}")
        raise TimeoutError(location)

    def find(self, kind: str, name: str) -> str | None:
        items = self.call("GET", f"/{kind}").get("value", [])
        return next((i["id"] for i in items if i.get("displayName") == name), None)

    def ensure(self, kind: str, name: str, body: dict | None = None) -> str:
        found = self.find(kind, name)
        if found:
            print(f"  = {kind[:-1]} {name} exists ({found})")
            return found
        created = self.call("POST", f"/{kind}", json={"displayName": name, **(body or {})})
        print(f"  + {kind[:-1]} {name} created")
        return created.get("id", "<new>")


def b64(path: pathlib.Path) -> str:
    return base64.b64encode(path.read_bytes()).decode()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workspace-id", required=True)
    ap.add_argument("--apply", action="store_true", help="really call the API (default: dry run)")
    args = ap.parse_args()
    fab = Fabric(args.workspace_id, args.apply)

    print("1. Lakehouse")
    fab.ensure("lakehouses", "lh_smartbike")

    print("2. Environment + smartbike wheel")
    wheels = sorted((ROOT / "dist").glob("smartbike-*-py3-none-any.whl"))
    if not wheels:
        print("  build it first: pip wheel . --no-deps -w dist")
        return 1
    env_id = fab.ensure("environments", "env_smartbike")
    with wheels[-1].open("rb") as fh:
        fab.call("POST", f"/environments/{env_id}/staging/libraries", files={"file": (wheels[-1].name, fh)})
    fab.call("POST", f"/environments/{env_id}/staging/publish")
    print(f"  ~ {wheels[-1].name} uploaded, environment published (add ortools + pandera from PyPI in the portal)")

    print("3. Notebooks")
    for nb in sorted((ROOT / "notebooks" / "fabric").glob("nb_*.ipynb")):
        definition = {"format": "ipynb", "parts": [
            {"path": "notebook-content.ipynb", "payload": b64(nb), "payloadType": "InlineBase64"}]}
        nb_id = fab.find("notebooks", nb.stem)
        if nb_id:
            fab.call("POST", f"/notebooks/{nb_id}/updateDefinition", json={"definition": definition})
            print(f"  ~ {nb.stem} updated")
        else:
            fab.call("POST", "/notebooks", json={"displayName": nb.stem, "definition": definition})
            print(f"  + {nb.stem} created")
    print("done" if args.apply else "dry run only - add --apply to deploy")
    return 0


if __name__ == "__main__":
    sys.exit(main())
