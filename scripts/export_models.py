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
# skops (safe serialisation) refuses unknown types; HistGradientBoosting needs exactly this one. We trained the
# model ourselves, so it is trusted - and nothing else is.
TRUSTED_TYPES = ["sklearn.ensemble._hist_gradient_boosting.predictor.TreePredictor"]


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
        mlflow.sklearn.save_model(model, OUT / target, serialization_format="skops", skops_trusted_types=TRUSTED_TYPES)
        run = client.get_run(mv.run_id) if mv.run_id else None
        manifest["models"][target] = {
            "registered_model": name, "version": int(mv.version), "alias": "champion", "run_id": mv.run_id,
            "metrics": {k: round(v, 4) for k, v in (run.data.metrics.items() if run else []) if "mae" in k or "rmse" in k},
            "features": list(model.feature_names_in_)}
        print(f"exported {name} v{mv.version} -> {OUT / target}")
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
