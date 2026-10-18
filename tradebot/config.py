"""Configuration: defaults + user overrides + secrets from .env.

* ``config/default_settings.yaml`` ships with the code and is never edited.
* ``config/settings.yaml`` holds only the user's overrides.
* Secrets (API keys, SMTP passwords) live only in ``.env`` / environment
  variables and are never written to settings files, logs or the dashboard.
"""

from __future__ import annotations

import copy
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from . import paths

VALID_MODES = ("local_paper", "broker_paper", "live")
VALID_PROVIDERS = ("yahoo", "stooq", "eodhd", "synthetic")

# Settings that can never be switched on in this version, whatever a file says.
LOCKED_FALSE = (("risk", "allow_short"), ("risk", "allow_leverage"))

# (section, key, min, max) sanity bounds; values outside are refused.
NUMERIC_BOUNDS = [
    ("risk", "max_risk_per_trade_pct", 0.01, 2.0),
    ("risk", "max_daily_loss_pct", 0.1, 5.0),
    ("risk", "max_drawdown_pct", 0.5, 25.0),
    ("risk", "max_position_pct", 1.0, 50.0),
    ("risk", "max_total_exposure_pct", 1.0, 100.0),
    ("risk", "max_sector_pct", 5.0, 100.0),
    ("risk", "max_open_positions", 1, 30),
    ("risk", "max_correlated_positions", 1, 30),
    ("risk", "correlation_threshold", 0.3, 1.0),
    ("risk", "min_cash_reserve_pct", 0.0, 50.0),
    ("risk", "max_pct_of_avg_volume", 0.01, 10.0),
    ("risk", "max_cost_pct_of_risk", 1.0, 100.0),
    ("risk", "failure_pause_threshold", 1, 20),
    ("paper", "starting_capital", 100, 10_000_000),
    ("data", "history_years", 1, 40),
    ("data", "stale_after_sessions", 0, 5),
    ("costs", "slippage_bps", 0, 500),
    ("costs", "stop_slippage_bps", 0, 1000),
]


class ConfigError(ValueError):
    pass


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _diff(base: dict, current: dict) -> dict:
    """Return only the keys in ``current`` that differ from ``base``."""
    out: dict = {}
    for k, v in current.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            d = _diff(base[k], v)
            if d:
                out[k] = d
        elif base.get(k) != v:
            out[k] = v
    return out


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path.name} must contain a mapping of settings")
    return data


def validate(settings: dict) -> list[str]:
    """Return a list of problems; empty when the settings are acceptable."""
    problems: list[str] = []
    mode = settings.get("app", {}).get("mode")
    if mode not in VALID_MODES:
        problems.append(f"app.mode must be one of {VALID_MODES}, got {mode!r}")
    prov = settings.get("data", {}).get("provider")
    if prov not in VALID_PROVIDERS:
        problems.append(f"data.provider must be one of {VALID_PROVIDERS}, got {prov!r}")
    for section, key in LOCKED_FALSE:
        if settings.get(section, {}).get(key):
            problems.append(f"{section}.{key} cannot be enabled in this version")
    for section, key, lo, hi in NUMERIC_BOUNDS:
        val = settings.get(section, {}).get(key)
        if not isinstance(val, (int, float)) or isinstance(val, bool):
            problems.append(f"{section}.{key} must be a number")
        elif not lo <= val <= hi:
            problems.append(f"{section}.{key}={val} is outside the allowed range {lo}..{hi}")
    profile = settings.get("paper", {}).get("cost_profile")
    if profile not in settings.get("costs", {}).get("profiles", {}):
        problems.append(f"paper.cost_profile {profile!r} is not a defined cost profile")
    return problems


def load_settings(user_file: Path | None = None, overrides: dict | None = None) -> dict:
    defaults = _read_yaml(paths.DEFAULT_SETTINGS_FILE)
    user = _read_yaml(user_file or paths.USER_SETTINGS_FILE)
    settings = _deep_merge(defaults, user)
    if overrides:
        settings = _deep_merge(settings, overrides)
    problems = validate(settings)
    if problems:
        raise ConfigError("Invalid settings: " + "; ".join(problems))
    return settings


def save_user_settings(settings: dict, user_file: Path | None = None) -> Path:
    """Validate and persist only the differences from the defaults.

    The previous file is copied to ``backups/`` first, so a bad change can be
    undone.
    """
    problems = validate(settings)
    if problems:
        raise ConfigError("Invalid settings: " + "; ".join(problems))
    target = user_file or paths.USER_SETTINGS_FILE
    if target.exists():
        paths.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        shutil.copy2(target, paths.BACKUP_DIR / f"settings-{stamp}.yaml")
    diff = _diff(_read_yaml(paths.DEFAULT_SETTINGS_FILE), settings)
    header = "# Your settings. Only values that differ from default_settings.yaml are stored.\n"
    tmp = target.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        fh.write(header)
        yaml.safe_dump(diff, fh, sort_keys=False, allow_unicode=True)
    os.replace(tmp, target)
    return target


def ensure_user_settings() -> None:
    if not paths.USER_SETTINGS_FILE.exists():
        paths.USER_SETTINGS_FILE.write_text(
            "# Your settings. Only values that differ from default_settings.yaml are stored.\n{}\n",
            encoding="utf-8",
        )


def load_universe(settings: dict) -> list[dict]:
    path = paths.PROJECT_ROOT / settings["universe"]["file"]
    data = _read_yaml(path)
    out: list[dict] = []
    seen: set[str] = set()
    for raw in data.get("instruments", []):
        sym = str(raw.get("symbol", "")).strip().upper()
        if not sym or sym in seen:
            continue
        seen.add(sym)
        itype = raw.get("type", "stock")
        if itype not in ("stock", "etf"):
            continue  # complex instruments are not supported
        if raw.get("leveraged"):
            continue
        out.append({
            "symbol": sym,
            "name": raw.get("name", sym),
            "type": itype,
            "sector": raw.get("sector", "Unknown"),
            "tier": raw.get("tier", "default"),
            "currency": str(raw.get("currency", "auto")).upper() if raw.get("currency") else "AUTO",
            "exchange": "LSE" if sym.endswith(".L") else "US",
            "enabled": bool(raw.get("enabled", True)),
        })
    return out


# --------------------------------------------------------------------------
# Secrets (.env) - minimal parser, values never logged.
# --------------------------------------------------------------------------
_SECRET_NAMES: set[str] = set()


def load_env_file(path: Path | None = None) -> None:
    path = path or paths.ENV_FILE
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
        _SECRET_NAMES.add(key)


def get_secret(name: str) -> str | None:
    _SECRET_NAMES.add(name)
    val = os.environ.get(name)
    return val or None


def secret_values() -> list[str]:
    """Current values of known secrets, used by the log redaction filter."""
    vals = []
    for n in _SECRET_NAMES | {"EODHD_API_TOKEN", "SMTP_PASSWORD", "TRADEBOT_LIVE_AUTHORISED"}:
        v = os.environ.get(n)
        if v and len(v) >= 4:
            vals.append(v)
    return vals
