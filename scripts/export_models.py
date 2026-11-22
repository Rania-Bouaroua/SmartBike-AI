"""Export the production (champion) demand models from the MLflow registry into a self-contained serving bundle.

Why: the registry stores artifact locations as absolute paths of the training machine (file:C:/Users/...), so a
container, another laptop or an App Service cannot resolve `models:/...@champion`. Serving should not depend on the
training registry anyway: we promote an immutable artifact + a manifest, and the API/app load that.

    python scripts/export_models.py            # -> models/serving/{departures,arrivals}/ + manifest.json

The serving layer uses it when SMARTBIKE_MODEL_DIR points to the bundle (docker-compose sets it).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "models" / "serving"
MODEL_NAME = "smartbike_demand_model"


def trusted_types(model) -> list[str]:
    """skops (safe serialisation) refuses types it does not know. We trust exactly the types of the champion we
    trained ourselves (e.g. TreePredictor for gradient boosting, Tree for forests) - and nothing else."""
    import skops.io
    return skops.io.get_untrusted_types(data=skops.io.dumps(model))


def main() -> int:
    import mlflow
    from mlflow import MlflowClient

    mlflow.set_tracking_uri(os.environ.get("MLFLOW_TRACKING_URI", f"sqlite:///{ROOT.as_posix()}/mlruns.db"))
    client = MlflowClient()
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True)
    manifest = {"exported_at_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"), "models": {}}
    for target in ("departures", "arrivals"):
        name = f"{MODEL_NAME}_{target}"
        mv = client.get_model_version_by_alias(name, "champion")
        model = mlflow.sklearn.load_model(f"models:/{name}@champion")
        mlflow.sklearn.save_model(model, OUT / target, serialization_format="skops", skops_trusted_types=trusted_types(model))
        run = client.get_run(mv.run_id) if mv.run_id else None
        manifest["models"][target] = {
            "registered_model": name, "algorithm": type(model).__name__, "version": int(mv.version),
            "alias": "champion", "run_id": mv.run_id,
            "metrics": {k: round(v, 4) for k, v in (run.data.metrics.items() if run else [])
                        if any(s in k for s in ("mae", "rmse", "dev"))},
            "features": list(model.feature_names_in_)}
        print(f"exported {name} v{mv.version} -> {OUT / target}")
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
