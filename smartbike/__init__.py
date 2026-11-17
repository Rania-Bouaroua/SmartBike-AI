"""SmartBike AI - domain logic shared by the notebooks, the API, the operator app and the evaluation scripts.

Pure Python (pandas / numpy): no Spark, no file system, no network, so every rule is unit-testable and behaves the
same locally and in Microsoft Fabric (where the package is attached to the Fabric Environment as a wheel).

Modules
    features    model input features (identical to training, notebook 03)
    risk        point projection rule + probabilistic stock-out risk (Monte Carlo on Poisson demand)
    routing     rebalancing truck tours (capacitated pickup & delivery, OR-Tools) + greedy baseline
    evaluation  alert backtest metrics (precision / recall / PR-AUC / Brier / calibration)
"""
__version__ = "1.1.0"
