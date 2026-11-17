"""Convert the percent-format notebooks (notebooks/*.py, cells separated by '# %%') to .ipynb
so they can be imported in Microsoft Fabric (Workspace > Import > Notebook > From this computer).

Usage: python scripts/py_to_ipynb.py   -> writes notebooks/fabric/*.ipynb
"""
import json
import pathlib
import re

SRC = pathlib.Path("notebooks")
OUT = SRC / "fabric"


def convert(path: pathlib.Path) -> dict:
    text = path.read_text(encoding="utf-8")
    chunks = re.split(r"^# %%", text, flags=re.M)
    cells = []
    for chunk in chunks:
        if not chunk.strip():
            continue
        header, _, body = chunk.partition("\n")
        if header.strip().startswith("[markdown]"):
            lines = [re.sub(r"^# ?", "", ln) for ln in body.strip("\n").splitlines()]
            cells.append({"cell_type": "markdown", "metadata": {}, "source": "\n".join(lines)})
        else:
            title = header.strip(" -")
            src = (f"# {title}\n" if title else "") + body.strip("\n")
            cells.append({"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [], "source": src})
    return {"cells": cells, "nbformat": 4, "nbformat_minor": 5,
            "metadata": {"kernelspec": {"name": "synapse_pyspark", "display_name": "Synapse PySpark"},
                         "language_info": {"name": "python"}}}


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    for p in sorted(SRC.glob("0*.py")):
        nb = convert(p)
        target = OUT / f"nb_{p.stem}.ipynb"
        target.write_text(json.dumps(nb, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"{target}  ({len(nb['cells'])} cells)")
