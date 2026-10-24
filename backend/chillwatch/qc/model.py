"""ML layer: LightGBM model predicting a station's hourly temperature from its neighbors.

The target's own temperature at time t is never a feature (see ``features.py``).
Fallback order: model -> IDW + lapse baseline (``idw_lapse``) -> NaN (``none``).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.model_selection import GroupKFold, GroupShuffleSplit

from .. import config
from ..normalize import to_pst
from ..repair import uncertainty as unc
from ..timeutil import season_bounds, utc
from .features import EXTRA_FEATURES, feature_names, is_night, location_features, station_features
from .neighbors import Field, build_field
from .rules import stuck_runs

log = logging.getLogger(__name__)
PRED_COLUMNS = ["ts", "expected_f", "est_sd", "p10", "p90", "n_neighbors", "method"]


# =========================================================================== bundle
@dataclass
class ModelBundle:
    booster: lgb.Booster | None
    features: list[str]
    unc_model: dict[str, Any]
    unc_idw: dict[str, Any]
    version: str
    path: Path | None = None
    metrics: dict[str, Any] = field(default_factory=dict)
    _oof: pd.DataFrame | None = None

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        if self.booster is None:
            return np.full(len(X), np.nan)
        cols = {c: (X[c] if c in X.columns else np.nan) for c in self.features}
        return np.asarray(
            self.booster.predict(pd.DataFrame(cols, index=X.index)[self.features].to_numpy(dtype="float64"))
        )

    def save(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        if self.booster is not None:
            self.booster.save_model(str(path / "model.txt"))
        (path / "features.json").write_text(json.dumps(self.features))
        (path / "uncertainty.json").write_text(json.dumps({"model": self.unc_model, "idw_lapse": self.unc_idw}))
        (path / "metrics.json").write_text(json.dumps(self.metrics, indent=2, default=str))
        if self._oof is not None:
            self._oof.to_parquet(path / "oof.parquet", index=False)
        self.path = path

    @classmethod
    def load(cls, path: str | Path) -> ModelBundle:
        path = Path(path)
        booster = lgb.Booster(model_file=str(path / "model.txt")) if (path / "model.txt").exists() else None
        u = json.loads((path / "uncertainty.json").read_text())
        metrics = json.loads((path / "metrics.json").read_text()) if (path / "metrics.json").exists() else {}
        oof = pd.read_parquet(path / "oof.parquet") if (path / "oof.parquet").exists() else None
        return cls(
            booster=booster,
            features=json.loads((path / "features.json").read_text()),
            unc_model=u["model"],
            unc_idw=u["idw_lapse"],
            version=path.name,
            path=path,
            metrics=metrics,
            _oof=oof,
        )

    def oof_blocks(self) -> np.ndarray:
        """(n, 24) LOSO residual blocks over whole local days (for bootstrap traces)."""
        if self._oof is None or self._oof.empty:
            sd = self.unc_model.get("_all", {}).get("sd", 2.0)
            rng = np.random.default_rng(0)
            return rng.normal(0, sd, size=(500, 24))
        o = self._oof.copy()
        t = pd.DatetimeIndex(o["ts"]).tz_convert("UTC").tz_localize(None) - pd.Timedelta(hours=9)
        o["day"] = t.normalize()
        o["h"] = t.hour
        piv = o.pivot_table(index=["station_id", "day"], columns="h", values="residual")
        piv = piv.dropna()
        return piv.to_numpy(dtype="float64") if len(piv) else np.zeros((1, 24))


def idw_only_bundle() -> ModelBundle:
    """Physics-only fallback when no trained model is available."""
    flat = {"sd": 3.0, "q10": -3.8, "q90": 3.8, "n": 0, "bias": 0.0}  # TODO source: placeholder until a model is trained
    table = {"_all": flat, "_pooled_n": flat, "_pooled_d": flat, "_widest": flat}
    return ModelBundle(booster=None, features=feature_names(), unc_model=table, unc_idw=table, version="idw-only")


# =========================================================================== training data
def _params(seed: int | None = None) -> dict[str, Any]:
    m = config.thresholds()["model"]
    return {
        "objective": m["objective"],
        "alpha": m["huber_alpha"],
        "num_leaves": m["num_leaves"],
        "learning_rate": m["learning_rate"],
        "min_data_in_leaf": m["min_data_in_leaf"],
        "feature_fraction": m["feature_fraction"],
        "bagging_seed": seed if seed is not None else m["seed"],
        "feature_fraction_seed": seed if seed is not None else m["seed"],
        "seed": seed if seed is not None else m["seed"],
        "deterministic": True,
        "force_row_wise": True,
        "verbose": -1,
        "num_threads": int(os.environ.get("CHILLWATCH_LGB_THREADS", m.get("num_threads", 0))),
    }


def training_frame(
    fld: Field,
    station_ids: list[int] | None = None,
    prism: dict[int, pd.DataFrame] | None = None,
    mask_prism: float = 0.0,
    rng: np.random.Generator | None = None,
) -> pd.DataFrame:
    """Rows = (station, hour) with a valid label; label from ``fld.W`` (already QC-filtered)."""
    frames = []
    ids = station_ids if station_ids is not None else [int(s) for s in fld.station_ids]
    for sid in ids:
        if sid not in fld.pos:
            continue
        X, aux = station_features(fld, sid, prism=(prism or {}).get(sid))
        y = fld.W[:, fld.pos[sid]].copy()
        # rule-invalid labels: stuck runs (identical values while neighbors move)
        for s0, e0, _ in stuck_runs(fld.raw[:, fld.pos[sid]], aux["nbr_median"]):
            y[s0:e0 + 1] = np.nan
        keep = np.isfinite(y) & (aux["n_valid"] >= 1)
        if not keep.any():
            continue
        X = X[keep].astype("float32")
        if mask_prism and "prism_tmin" in X.columns and rng is not None:
            m = rng.random(len(X)) < mask_prism
            X.loc[m, ["prism_tmin", "prism_tmax"]] = np.nan
        X["y"] = y[keep]
        X["station_id"] = sid
        X["ts"] = fld.hours[keep]
        X["night"] = is_night(fld.hours[keep])
        X["nearest_km"] = aux["nearest_km"][keep]
        X["nearest_dz_ft"] = aux["nearest_dz_ft"][keep]
        frames.append(X)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def _log_progress(period: int, tag: str) -> Any:
    def cb(env: Any) -> None:
        if period and (env.iteration + 1) % period == 0:
            vals = ", ".join(f"{name} {value:.4f}" for _, name, value, _ in env.evaluation_result_list)
            log.info("%s round %d %s", tag, env.iteration + 1, vals)
    return cb


def _fit(train: pd.DataFrame, feats: list[str], seed: int, num_round: int | None = None,
         tag: str = "fit") -> lgb.Booster:
    m = config.thresholds()["model"]
    params = _params(seed)
    if num_round is not None:
        ds = lgb.Dataset(train[feats].to_numpy(dtype="float32"), train["y"].to_numpy(), feature_name=feats)
        return lgb.train(params, ds, num_boost_round=max(1, num_round))
    gss = GroupShuffleSplit(n_splits=1, test_size=float(m["valid_station_fraction"]), random_state=seed)
    tr_idx, va_idx = next(gss.split(train, groups=train["station_id"]))
    tr, va = train.iloc[tr_idx], train.iloc[va_idx]
    dtr = lgb.Dataset(tr[feats].to_numpy(dtype="float32"), tr["y"].to_numpy(), feature_name=feats)
    dva = lgb.Dataset(va[feats].to_numpy(dtype="float32"), va["y"].to_numpy(), reference=dtr)
    b = lgb.train(
        params,
        dtr,
        num_boost_round=int(m["num_boost_round"]),
        valid_sets=[dva],
        valid_names=["held-out stations"],
        callbacks=[lgb.early_stopping(int(m["early_stopping_rounds"]), verbose=False),
                   _log_progress(int(m.get("log_every", 100)), tag)],
    )
    log.info("%s: best round %d", tag, b.best_iteration or b.current_iteration())
    return b


def robust_sigma(r: np.ndarray) -> float:
    r = r[np.isfinite(r)]
    if len(r) == 0:
        return float("nan")
    return float(1.4826 * np.median(np.abs(r - np.median(r))))


def fit_model(train: pd.DataFrame, feats: list[str], seed: int, tag: str = "model"
              ) -> tuple[lgb.Booster, dict[str, Any]]:
    """Fit with early stopping, drop |residual| > 5 robust sigma (dirty labels), refit once."""
    m = config.thresholds()["model"]
    b = _fit(train, feats, seed, tag=f"{tag} first fit")
    r = train["y"].to_numpy() - b.predict(train[feats].to_numpy(dtype="float32"))
    s = robust_sigma(r)
    keep = np.abs(r) <= float(m["dirty_label_sigma"]) * max(s, 1e-6)
    info = {"rows": int(len(train)), "dirty_removed": int((~keep).sum()), "sigma_first_fit": s,
            "best_iter_first": int(b.best_iteration or b.current_iteration())}
    b2 = _fit(train[keep], feats, seed, tag=f"{tag} refit")
    info["best_iter"] = int(b2.best_iteration or b2.current_iteration())
    return b2, info


# =========================================================================== metrics
def chill_hours(temps: np.ndarray) -> float:
    c = config.thresholds()["chill"]
    t = np.asarray(temps, dtype="float64")
    return float(np.sum((t >= c["min_f"]) & (t <= c["max_f"])))


def chill_mask(ts: pd.Series | pd.DatetimeIndex) -> np.ndarray:
    """Hours counted for chill: PST dates from chill.start_mmdd (Nov 1) through chill.end_mmdd (Mar 31)."""
    c = config.thresholds()["chill"]
    start, end = c["start_mmdd"], c.get("end_mmdd", "06-30")
    local = pd.DatetimeIndex(ts).tz_convert("UTC").tz_localize(None) - pd.Timedelta(hours=8, seconds=1)
    mmdd = local.strftime("%m-%d")
    return np.asarray((mmdd >= start) | (mmdd <= end)) if start > end else np.asarray((mmdd >= start) & (mmdd <= end))


def error_report(df: pd.DataFrame, pred_col: str = "pred") -> dict[str, Any]:
    """MAE overall / night-day / distance bins / |dz| bins; chill-hour error per station-season."""
    u = config.thresholds()["uncertainty"]
    d = df[np.isfinite(df[pred_col]) & np.isfinite(df["y"])].copy()
    d["ae"] = (d["y"] - d[pred_col]).abs()
    out: dict[str, Any] = {"n": int(len(d)), "mae_f": float(d["ae"].mean()) if len(d) else None}
    out["mae_night_f"] = float(d.loc[d["night"], "ae"].mean()) if d["night"].any() else None
    out["mae_day_f"] = float(d.loc[~d["night"], "ae"].mean()) if (~d["night"]).any() else None
    db = [0, *u["distance_bins_km"], np.inf]
    zb = [0, *u["dz_bins_ft"], np.inf]
    dl = [f"<{db[1]:g}km", f"{db[1]:g}-{db[2]:g}km", f">{db[2]:g}km"]
    zl = [f"<{zb[1]:g}ft", f"{zb[1]:g}-{zb[2]:g}ft", f">{zb[2]:g}ft"]
    d["dbin"] = pd.cut(d["nearest_km"], db, labels=dl, right=False)
    d["zbin"] = pd.cut(d["nearest_dz_ft"].abs(), zb, labels=zl, right=False)
    out["mae_by_distance"] = {str(k): {"mae_f": float(g["ae"].mean()), "n": int(len(g))}
                              for k, g in d.groupby("dbin", observed=True)}
    out["mae_by_dz"] = {str(k): {"mae_f": float(g["ae"].mean()), "n": int(len(g))}
                        for k, g in d.groupby("zbin", observed=True)}
    # chill hours, per station-season, on hours where both exist
    from ..timeutil import season_labels

    d["season"] = season_labels(pd.DatetimeIndex(d["ts"])).to_numpy()
    d["chill"] = chill_mask(pd.DatetimeIndex(d["ts"]))
    errs = []
    for (_, _), g in d[d["chill"]].groupby(["station_id", "season"]):
        if len(g) < 24 * 30:
            continue
        errs.append(chill_hours(g[pred_col].to_numpy()) - chill_hours(g["y"].to_numpy()))
    if errs:
        e = np.asarray(errs)
        out["chill_hours_error"] = {
            "station_seasons": int(len(e)),
            "mean_abs": float(np.mean(np.abs(e))),
            "median_abs": float(np.median(np.abs(e))),
            "mean_signed": float(np.mean(e)),
            "p90_abs": float(np.quantile(np.abs(e), 0.9)),
        }
    return out


# =========================================================================== train
def slim_readings(r: pd.DataFrame) -> pd.DataFrame:
    """Only what building training frames needs (numeric, compact): no raw paths or estimates."""
    keep = ["station_id", "ts", "temp_f", "qc_status", *[c for c in EXTRA_FEATURES if c in r.columns]]
    out = r[keep].copy()
    for c in ("temp_f", *[c for c in EXTRA_FEATURES if c in r.columns]):
        out[c] = out[c].astype("float32")
    out["qc_status"] = out["qc_status"].astype("category")
    return out


def _season_frame(job: tuple[Any, ...]) -> tuple[str, pd.DataFrame, set[str]]:
    """One season's training rows (runs in a worker process)."""
    season, r, stations, start, end, extras = job
    # severe source flags drop via qc_status; range-invalid via build_field; stuck in training_frame
    fld = build_field(r, stations, start, end, valid_col="qc_status", valid_values=("ok",), extras=extras)
    found = {c for c, a in fld.extras.items() if np.isfinite(a).any()}
    tf = training_frame(fld)
    if len(tf):
        tf["season"] = season
    return season, tf, found


def train(
    store: Any,
    seasons: list[str] | None = None,
    version: str | None = None,
    loso_folds: int | None = None,
    out_dir: Path | None = None,
    use_extras: bool = True,
    max_stations: int | None = None,
    drop_ids: set[int] | None = None,
    max_rows: int | None = None,
    activate: bool = True,
    fold_procedure: str | None = None,
    evaluate_holdout: bool = True,
) -> ModelBundle:
    """Train the neighbor model on non-holdout seasons and run LOSO validation.

    ``drop_ids`` removes stations entirely (as targets and as neighbors), for blind tests.
    ``loso_folds=1`` skips cross-validation (uncertainty then comes from in-sample residuals).
    """
    th = config.thresholds()
    m = th["model"]
    reg = config.region()
    from ..timeutil import seasons_between

    all_seasons = seasons or seasons_between(reg["seasons"]["first"], reg["seasons"]["last"])
    skip = set(reg.get("holdout_seasons", [])) | set(reg.get("exclude_from_training", []))
    train_seasons = [s for s in all_seasons if s not in skip]
    stations = store.stations()
    stations = stations[stations.get("in_region", True).astype(bool)] if "in_region" in stations else stations
    if max_stations:
        stations = stations.head(max_stations)
    if drop_ids:
        stations = stations[~stations["station_id"].isin(list(drop_ids))]
    seed = int(m["seed"])
    frames = []
    has_extras: set[str] = set()
    jobs = []
    for s in train_seasons:
        start, end = season_bounds(s, reg["seasons"]["start_mmdd"], reg["seasons"]["end_mmdd"])
        r = slim_readings(store.readings(start, end, stations["station_id"].tolist()))
        if r.empty or r["temp_f"].notna().sum() == 0:
            continue
        jobs.append((s, r, start, end))
    workers = max(1, min(len(jobs), int(m.get("build_workers", 4))))
    extras = tuple(EXTRA_FEATURES) if use_extras else ()
    if workers > 1:
        from concurrent.futures import ProcessPoolExecutor

        with ProcessPoolExecutor(max_workers=workers) as ex:
            built = list(ex.map(_season_frame, [(s, r, stations, a, b, extras) for s, r, a, b in jobs]))
    else:
        built = [_season_frame((s, r, stations, a, b, extras)) for s, r, a, b in jobs]
    for s, tf, ex_found in built:
        has_extras |= ex_found
        if len(tf):
            frames.append(tf)
        log.info("season %s: %d rows", s, len(tf))
    if not frames:
        raise RuntimeError("no training data: run `chillwatch backfill` first")
    data = pd.concat(frames, ignore_index=True)
    if max_rows and len(data) > max_rows:
        data = data.sample(n=max_rows, random_state=seed).sort_values(["station_id", "ts"]).reset_index(drop=True)
    feats = feature_names(extras=tuple(c for c in EXTRA_FEATURES if c in has_extras))
    for f in feats:
        if f not in data.columns:
            data[f] = np.nan
    booster, info = fit_model(data, feats, seed)
    data["t_base_pred"] = data["t_base"]
    holdout_report = None
    if evaluate_holdout and not drop_ids:
        holdout_report = _holdout_known_stations(store, stations, booster, feats, data, reg, extras, workers)

    # ---------------- LOSO / GroupKFold by station
    folds = int(loso_folds if loso_folds is not None else m["loso_folds"])
    groups = data["station_id"].to_numpy()
    n_groups = len(np.unique(groups))
    n_splits = n_groups if folds == 0 else min(folds, n_groups)
    oof = np.full(len(data), np.nan)
    fold_procedure = str(fold_procedure or m.get("loso_procedure", "full"))
    fold_info = []
    if n_splits >= 2:
        for k, (tr, va) in enumerate(GroupKFold(n_splits=n_splits).split(data, groups=groups)):
            if fold_procedure == "full":
                # the shipped procedure inside the fold: early stopping on the fold's own training
                # stations + dirty-label removal, so no validation-fold station informs the fit
                b, fi = fit_model(data.iloc[tr], feats, seed, tag=f"fold {k + 1}/{n_splits}")
                fold_info.append(fi)
            else:
                b = _fit(data.iloc[tr], feats, seed, num_round=info["best_iter"])
            oof[va] = b.predict(data.iloc[va][feats].to_numpy(dtype="float32"))
            log.info("LOSO fold %d/%d done", k + 1, n_splits)
    if n_splits < 2:  # no CV requested: in-sample residuals (optimistic; labeled in metrics)
        oof = np.asarray(booster.predict(data[feats].to_numpy(dtype="float32")))
    data["pred"] = oof
    data["residual"] = data["y"] - data["pred"]
    data["residual_idw"] = data["y"] - data["t_base"]

    metrics = {
        "version": version,
        "trained_at": datetime.now().astimezone().isoformat(),
        "train_seasons": [s for s in train_seasons if s in set(data["season"])],
        "holdout_seasons": reg.get("holdout_seasons", []),
        "features": feats,
        "fit": info,
        "validation": (f"GroupKFold by station, {n_splits} folds, fold models trained with "
                       + ("the full procedure (own early stopping + dirty-label removal)" if fold_procedure == "full"
                          else "the main model's round count")) if n_splits >= 2 else "none (in-sample)",
        "fold_fits": fold_info,
        "dropped_stations": sorted(int(x) for x in (drop_ids or ())),
        "n_stations": int(n_groups),
    }
    key = "loso" if n_splits >= 2 else "in_sample"
    common = np.isfinite(data["pred"].to_numpy()) & np.isfinite(data["t_base"].to_numpy())
    metrics[f"{key}_model"] = error_report(data, "pred")
    metrics[f"{key}_idw_lapse"] = error_report(data.assign(pred_idw=data["t_base"]), "pred_idw")
    # like-for-like comparison: the same hours for both (the model also covers 1-neighbor hours)
    if holdout_report is not None:
        metrics["holdout_known_stations"] = holdout_report
    metrics[f"{key}_common_rows"] = {
        "model": error_report(data[common], "pred"),
        "idw_lapse": error_report(data[common].assign(pred_idw=data.loc[common, "t_base"]), "pred_idw"),
    }
    ok = np.isfinite(data["residual"].to_numpy())
    unc_model = unc.fit_bins(
        data["residual"].to_numpy()[ok], data["nearest_km"].to_numpy()[ok],
        data["nearest_dz_ft"].to_numpy()[ok], data["night"].to_numpy()[ok],
    )
    ok2 = np.isfinite(data["residual_idw"].to_numpy())
    unc_idw = unc.fit_bins(
        data["residual_idw"].to_numpy()[ok2], data["nearest_km"].to_numpy()[ok2],
        data["nearest_dz_ft"].to_numpy()[ok2], data["night"].to_numpy()[ok2],
    )
    version = version or datetime.now().strftime("m%Y%m%d-%H%M%S")
    metrics["version"] = version
    bundle = ModelBundle(
        booster=booster, features=feats, unc_model=unc_model, unc_idw=unc_idw, version=version, metrics=metrics,
        _oof=data.loc[ok, ["station_id", "ts", "residual"]].reset_index(drop=True),
    )
    path = (out_dir or config.data_dir() / "models") / version
    bundle.save(path)
    if activate:
        (path.parent / "ACTIVE").write_text(version)
    _register(bundle, store, activate)
    return bundle


def _holdout_known_stations(store: Any, stations: pd.DataFrame, booster: lgb.Booster, feats: list[str],
                            data: pd.DataFrame, reg: dict[str, Any], extras: tuple[str, ...],
                            workers: int) -> dict[str, Any] | None:
    """Accuracy at stations the model was trained on, in the held-out (never trained) seasons.

    This is the QC / gap-fill situation. Per-station night/day offsets come from the most recent
    training season (median residual), as the production offsets would.
    """
    hold = list(reg.get("holdout_seasons", []))
    jobs = []
    for s in hold:
        a, b = season_bounds(s, reg["seasons"]["start_mmdd"], reg["seasons"]["end_mmdd"])
        r = slim_readings(store.readings(a, b, stations["station_id"].tolist()))
        if len(r) and r["temp_f"].notna().any():
            jobs.append((s, r, stations, a, b, extras))
    if not jobs:
        return None
    if workers > 1 and len(jobs) > 1:
        from concurrent.futures import ProcessPoolExecutor

        with ProcessPoolExecutor(max_workers=min(workers, len(jobs))) as ex:
            built = list(ex.map(_season_frame, jobs))
    else:
        built = [_season_frame(j) for j in jobs]
    frames = [tf for _, tf, _ in built if len(tf)]
    if not frames:
        return None
    h = pd.concat(frames, ignore_index=True)
    h = h[h["station_id"].isin(set(data["station_id"]))].reset_index(drop=True)
    for f in feats:
        if f not in h.columns:
            h[f] = np.nan
    h["pred"] = booster.predict(h[feats].to_numpy(dtype="float32"))
    last = data[data["season"] == data["season"].max()]
    r_last = last["y"].to_numpy() - booster.predict(last[feats].to_numpy(dtype="float32"))
    key = pd.MultiIndex.from_arrays([h["station_id"], h["night"]])
    off_m = pd.Series(r_last, index=pd.MultiIndex.from_arrays([last["station_id"], last["night"]])).groupby(level=[0, 1]).median()
    off_i = (last["y"] - last["t_base"]).groupby([last["station_id"], last["night"]]).median()
    h["pred_off"] = h["pred"] + off_m.reindex(key).fillna(0).to_numpy()
    h["idw"] = h["t_base"]
    h["idw_off"] = h["t_base"] + off_i.reindex(key).fillna(0).to_numpy()
    common = np.isfinite(h[["pred", "pred_off", "idw", "idw_off"]].to_numpy()).all(axis=1)
    hc = h[common]
    return {
        "seasons": [s for s, _, _ in built],
        "stations": int(hc["station_id"].nunique()),
        "offsets_from": str(data["season"].max()),
        "model": error_report(hc, "pred"),
        "model_plus_offset": error_report(hc, "pred_off"),
        "idw_lapse": error_report(hc, "idw"),
        "idw_lapse_plus_offset": error_report(hc, "idw_off"),
    }


def _register(bundle: ModelBundle, store: Any, active: bool) -> None:
    """Record the model in ``model_registry`` when the store is PostgreSQL."""
    engine = getattr(store, "engine", None)
    if engine is None:
        return
    from sqlalchemy import text

    with engine.begin() as conn:
        if active:
            conn.execute(text("update model_registry set is_active = false"))
        conn.execute(
            text("insert into model_registry (version, created_at, path, metrics, is_active) "
                 "values (:v, now(), :p, cast(:m as json), :a) on conflict (version) do update set "
                 "path = excluded.path, metrics = excluded.metrics, is_active = excluded.is_active"),
            {"v": bundle.version, "p": str(bundle.path), "m": json.dumps(bundle.metrics, default=str), "a": active},
        )


def active_model_path() -> Path | None:
    root = config.data_dir() / "models"
    f = root / "ACTIVE"
    if f.exists():
        p = root / f.read_text().strip()
        if p.exists():
            return p
    return None


# =========================================================================== inference
def predict_location_frame(
    fld: Field,
    bundle: ModelBundle,
    lat: float,
    lon: float,
    elevation_ft: float,
    exclude: set[int] | None = None,
    offsets: pd.DataFrame | None = None,
    prism: pd.DataFrame | None = None,
    use_model: bool = True,
) -> pd.DataFrame:
    """Expected temperature over ``fld.hours`` for a location (pure: field in, frame out).

    ``use_model=False`` gives the physics estimate (IDW + lapse) with its own uncertainty bins.
    """
    X, aux = location_features(fld, lat, lon, elevation_ft, exclude=exclude, prism=prism)
    n = aux["n_valid"]
    night = is_night(fld.hours)
    pred = bundle.predict(X) if (bundle.booster is not None and use_model) else np.full(len(X), np.nan)
    model_ok = np.isfinite(pred) & (n >= 1)
    # without the model (places with no station history): the fitted physics estimate
    tb = aux["t_base"] if use_model else aux["t_phys"]
    use_idw = ~model_ok & np.isfinite(tb)
    method = np.where(model_ok, "model", np.where(use_idw, "idw_lapse", "none"))
    exp = np.where(model_ok, pred, np.where(use_idw, tb, np.nan))
    sd_m, q10_m, q90_m = unc.lookup(bundle.unc_model, aux["nearest_km"], aux["nearest_dz_ft"], night)
    sd_i, q10_i, q90_i = unc.lookup(bundle.unc_idw, aux["nearest_km"], aux["nearest_dz_ft"], night)
    sd = np.where(model_ok, sd_m, np.where(use_idw, sd_i, np.nan))
    q10 = np.where(model_ok, q10_m, np.where(use_idw, q10_i, np.nan))
    q90 = np.where(model_ok, q90_m, np.where(use_idw, q90_i, np.nan))
    df = pd.DataFrame(
        {
            "ts": fld.hours,
            "expected_f": exp,
            "est_sd": sd,
            "p10": exp + q10,
            "p90": exp + q90,
            "n_neighbors": n.astype(int),
            "method": method,
            "night": night,
            "t_base": tb,
            "nbr_median": aux["nbr_median"],
            "nearest_km": aux["nearest_km"],
            "nearest_dz_ft": aux["nearest_dz_ft"],
            "offset_f": 0.0,
        }
    )
    return apply_offsets(df, offsets)


def apply_offsets(df: pd.DataFrame, offsets: pd.DataFrame | None) -> pd.DataFrame:
    """Add the per-station learned/accepted offset in force at each hour (expects one station)."""
    df = df.copy()
    if offsets is None or offsets.empty or df.empty:
        if "offset_f" not in df:
            df["offset_f"] = 0.0
        return df
    off = _offset_series(pd.DatetimeIndex(df["ts"]), df["night"].to_numpy(dtype=bool), offsets)
    base = df["offset_f"].to_numpy(dtype="float64") if "offset_f" in df else 0.0
    delta = off - base
    for c in ("expected_f", "p10", "p90"):
        df[c] = df[c] + delta
    df["offset_f"] = off
    return df


def _offset_series(hours: pd.DatetimeIndex, night: np.ndarray, offsets: pd.DataFrame) -> np.ndarray:
    """Offset in force at each hour: latest accepted one if any, else latest learned one."""
    o = offsets.copy()
    o["window_end"] = pd.to_datetime(o["window_end"], utc=True)
    o = o.sort_values(["window_end"], kind="stable")
    out = np.zeros(len(hours))
    t = pd.DatetimeIndex(hours).tz_convert("UTC").as_unit("ns").asi8
    for kind in ("learned", "accepted"):  # accepted applied last -> takes precedence
        ok = o[o["kind"] == kind]
        if ok.empty:
            continue
        ends = pd.DatetimeIndex(ok["window_end"]).as_unit("ns").asi8
        j = np.searchsorted(ends, t, side="right") - 1  # latest offset whose window ended at or before t
        has = j >= 0
        jj = np.clip(j, 0, None)
        val = np.where(night, ok["night_f"].to_numpy(dtype="float64")[jj], ok["day_f"].to_numpy(dtype="float64")[jj])
        if "hourly_f" in ok:  # 24 PST hour-of-day values; rows without them keep night/day
            table = np.array([json.loads(v) if isinstance(v, str) and v else [np.nan] * 24
                              for v in ok["hourly_f"]], dtype="float64").reshape(len(ok), 24)
            hv = table[jj, to_pst(pd.DatetimeIndex(hours)).hour.to_numpy()]
            val = np.where(np.isfinite(hv), hv, val)
        out = np.where(has & np.isfinite(val), val, out)
    return out


def predict_station_frame(
    fld: Field, bundle: ModelBundle, station_id: int, offsets: pd.DataFrame | None = None
) -> pd.DataFrame:
    row = fld.station_row(station_id)
    return predict_location_frame(
        fld, bundle, float(row.lat), float(row.lon), float(row.elevation_ft), exclude={int(station_id)},
        offsets=offsets,
    )


def predict_expected(station_id: int, start: datetime, end: datetime, ctx: Any | None = None) -> pd.DataFrame:
    """Expected hourly temperature for a station from its neighbors.

    Columns: ts, expected_f, est_sd, p10, p90, n_neighbors, method ("model"|"idw_lapse"|"none").
    """
    from ..context import get_context

    ctx = ctx or get_context()
    df = ctx.expected(start, end, [int(station_id)])
    return df[PRED_COLUMNS].reset_index(drop=True)


def predict_at_location(
    lat: float, lon: float, elevation_ft: float, start: datetime, end: datetime, ctx: Any | None = None
) -> pd.DataFrame:
    """Virtual station: expected hourly temperature at an arbitrary point (always an estimate)."""
    from ..context import get_context

    ctx = ctx or get_context()
    fld = ctx.field(start, end)
    df = predict_location_frame(fld, ctx.model, lat, lon, elevation_ft, use_model=unseen_uses_model())
    return df[PRED_COLUMNS].reset_index(drop=True)


def unseen_uses_model() -> bool:
    """Whether places with no station history use the ML model (else the physics estimate).

    Validation on CIMIS: for never-seen stations the model did not beat IDW + lapse, while at
    stations with history it did; see docs/DECISIONS.md.
    """
    return str(config.thresholds()["model"].get("unseen_method", "idw_lapse")) == "model"


# =========================================================================== offsets
def compute_offset(
    readings: pd.DataFrame, expected: pd.DataFrame
) -> tuple[float, float, int]:
    """Median residual (observed - model, before offsets) over ``ok`` readings, night and day."""
    d = readings.merge(expected, on="ts", how="inner")
    d = d[(d["qc_status"] == "ok") & d["temp_f"].notna() & d["expected_f"].notna()]
    r = d["temp_f"] - (d["expected_f"] - d.get("offset_f", 0.0))
    night = d["night"].astype(bool)
    n_f = float(np.median(r[night])) if night.any() else 0.0
    d_f = float(np.median(r[~night])) if (~night).any() else 0.0
    return n_f, d_f, int(len(d))


def hourly_offsets(readings: pd.DataFrame, expected: pd.DataFrame, night_f: float, day_f: float,
                   k: float | None = None) -> list[float]:
    """Per PST hour-of-day median residual, each pulled toward its night/day value with weight n / (n + k).

    24 values indexed by PST hour (0-23). Hourly bins follow the daily bias cycle better than two
    bins (bench/experiments/offset_bins.json); the pull keeps thin hours stable.
    """
    k = float(k if k is not None else config.thresholds()["offsets"].get("hour_shrink_k", 14))
    d = readings.merge(expected, on="ts", how="inner")
    d = d[(d["qc_status"] == "ok") & d["temp_f"].notna() & d["expected_f"].notna()]
    r = (d["temp_f"] - (d["expected_f"] - d.get("offset_f", 0.0))).to_numpy(dtype="float64")
    hour = to_pst(pd.DatetimeIndex(d["ts"])).hour.to_numpy()
    out = []
    for h in range(24):
        base = night_f if (h >= 19 or h <= 6) else day_f
        rh = r[hour == h]
        w = len(rh) / (len(rh) + k) if len(rh) else 0.0
        out.append(float(w * np.median(rh) + (1 - w) * base) if len(rh) else float(base))
    return out


def set_offset_from_window(
    station_id: int, start: datetime, end: datetime, ctx: Any | None = None, kind: str = "accepted"
) -> dict[str, Any]:
    """Record a station offset from a window (e.g. a grower confirms "it's on a hill")."""
    from ..context import get_context

    ctx = ctx or get_context()
    r = ctx.store.readings(start, end, [int(station_id)])
    e = ctx.expected(start, end, [int(station_id)], with_offsets=False)
    n_f, d_f, n = compute_offset(r, e)
    row = {
        "station_id": int(station_id),
        "kind": kind,
        "night_f": n_f,
        "day_f": d_f,
        "hourly_f": json.dumps(hourly_offsets(r, e, n_f, d_f)),
        "window_start": utc(start),
        "window_end": utc(end),
        "created_at": pd.Timestamp.now(tz="UTC"),
    }
    ctx.store.add_offset(row)
    ctx.invalidate()
    return {**row, "n": n}


def update_learned_offsets(as_of: datetime, ctx: Any | None = None, station_ids: list[int] | None = None) -> int:
    """Weekly job: per-station median residual (night/day and hour of day) over 28 days of ok readings.

    ``offsets.lag_days`` ends the 28-day window that many days before ``as_of``, so detection has
    that long to flag a new drift or offset before learning can absorb it as station bias;
    ``offsets.max_weekly_change_f`` limits how far a learned offset may move from the previous
    one. The offset takes effect at ``as_of`` (stored as ``window_end``; ``window_start`` is the
    start of the data window).
    """
    from ..context import get_context

    ctx = ctx or get_context()
    o = config.thresholds()["offsets"]
    days = int(o["window_days"])
    lag = pd.Timedelta(days=int(o.get("lag_days", 0)))
    cap = o.get("max_weekly_change_f")
    effective = utc(as_of)
    end = effective - lag
    start = end - pd.Timedelta(days=days)
    ids = station_ids or ctx.active_station_ids(effective)
    exp = ctx.expected(start, end, ids, with_offsets=False)
    rd = ctx.store.readings(start, end, ids)
    prev = ctx.store.offsets()
    if len(prev):
        prev = prev[(prev["kind"] == "learned") & (pd.to_datetime(prev["window_end"], utc=True) <= effective)]
    n_added = 0
    for sid in ids:
        e = exp[exp["station_id"] == sid]
        r = rd[rd["station_id"] == sid]
        if e.empty or r.empty:
            continue
        n_f, d_f, n = compute_offset(r, e)
        if n < 24 * 7:
            continue
        hourly = hourly_offsets(r, e, n_f, d_f)
        last = prev[prev["station_id"] == sid] if len(prev) else prev
        if cap is not None and len(last):
            last = last.sort_values("window_end").iloc[-1]
            c = float(cap)
            lh = json.loads(last["hourly_f"]) if isinstance(last.get("hourly_f"), str) else None
            n_f = float(np.clip(n_f, last["night_f"] - c, last["night_f"] + c))
            d_f = float(np.clip(d_f, last["day_f"] - c, last["day_f"] + c))
            if lh is not None:
                hourly = [float(np.clip(v, lv - c, lv + c)) for v, lv in zip(hourly, lh, strict=True)]
        ctx.store.add_offset({
            "station_id": int(sid), "kind": "learned", "night_f": n_f, "day_f": d_f,
            "hourly_f": json.dumps(hourly),
            "window_start": start, "window_end": effective, "created_at": pd.Timestamp.now(tz="UTC"),
        })
        n_added += 1
    ctx.invalidate()
    return n_added
