from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    host: str = "0.0.0.0"
    port: int = 8099
    log_level: str = "info"
    collection_interval_minutes: int = 60
    scoring_interval_minutes: int = 60
    auto_collection_enabled: bool = False
    ingress_mode: bool = True
    fred_api_key: str | None = None
    fiscal_api_key: str | None = None
    sec_user_agent: str | None = None
    historical_window_days: int = 1825

    @property
    def database_path(self) -> Path:
        return self.data_dir / "ai_bubble_monitor.db"

    @classmethod
    def load(cls) -> "Settings":
        data_dir = Path(os.environ.get("DATA_DIR", "/data"))
        options_path = data_dir / "options.json"
        options: dict[str, object] = {}
        try:
            loaded = json.loads(options_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                options = loaded
        except FileNotFoundError:
            pass
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Unable to read app options at {options_path}: {exc}") from exc

        allowed_levels = {"debug", "info", "warning", "error"}
        log_level = str(options.get("log_level", "info")).lower()
        if log_level not in allowed_levels:
            raise ValueError(f"Unsupported log_level: {log_level}")
        fred_api_key = os.environ.get("FRED_API_KEY") or options.get("fred_api_key") or ""
        fiscal_api_key = os.environ.get("FISCAL_API_KEY") or options.get("fiscal_api_key") or ""
        sec_user_agent = os.environ.get("SEC_USER_AGENT") or options.get("sec_user_agent") or ""
        auto_collection_raw = os.environ.get("AUTO_COLLECTION_ENABLED")
        if auto_collection_raw is None:
            auto_collection_raw = options.get("auto_collection_enabled", True)

        return cls(
            data_dir=data_dir,
            host=os.environ.get("HOST", "0.0.0.0"),
            port=int(os.environ.get("PORT", "8099")),
            log_level=log_level,
            collection_interval_minutes=_bounded_int(
                options.get("collection_interval_minutes", 60),
                "collection_interval_minutes",
                15,
                1440,
            ),
            scoring_interval_minutes=_bounded_int(
                options.get("scoring_interval_minutes", 60),
                "scoring_interval_minutes",
                15,
                1440,
            ),
            auto_collection_enabled=_boolean(auto_collection_raw, "auto_collection_enabled"),
            ingress_mode=os.environ.get("HA_INGRESS_MODE", "true").lower()
            not in {"0", "false", "no"},
            fred_api_key=str(fred_api_key).strip() or None,
            fiscal_api_key=str(fiscal_api_key).strip() or None,
            sec_user_agent=str(sec_user_agent).strip() or None,
            historical_window_days=_bounded_int(
                options.get("historical_window_days", 1825),
                "historical_window_days",
                365,
                3650,
            ),
        )


def _bounded_int(value: object, name: str, minimum: int, maximum: int) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not minimum <= result <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return result


def _boolean(value: object, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
    raise ValueError(f"{name} must be a boolean")
