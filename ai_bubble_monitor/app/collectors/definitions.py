from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class IndicatorDefinition:
    id: str
    name: str
    category: str
    unit: str
    frequency: str
    direction: str
    normalization_method: str
    source: str
    source_id: str
    staleness_days: int
    scenarios: dict[str, float]
    lookback_days: int = 1825
    minimum_observations: int = 60
    enabled: bool = True
    availability_note: str | None = None

    def as_configuration(self) -> dict[str, object]:
        return {
            "source": self.source,
            "source_id": self.source_id,
            "staleness_days": self.staleness_days,
            "scenarios": self.scenarios,
            "lookback_days": self.lookback_days,
            "minimum_observations": self.minimum_observations,
            "enabled": self.enabled,
            "availability_note": self.availability_note,
        }


def load_definitions(path: Path | None = None) -> list[IndicatorDefinition]:
    config_path = path or Path(__file__).resolve().parents[2] / "config" / "indicators.yaml"
    document = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or not isinstance(document.get("indicators"), list):
        raise ValueError(f"Invalid indicator catalog: {config_path}")

    definitions: list[IndicatorDefinition] = []
    seen: set[str] = set()
    for row in document["indicators"]:
        if not isinstance(row, dict):
            raise ValueError("Each indicator definition must be an object")
        definition = IndicatorDefinition(
            id=str(row["id"]),
            name=str(row["name"]),
            category=str(row["category"]),
            unit=str(row["unit"]),
            frequency=str(row["frequency"]),
            direction=str(row["direction"]),
            normalization_method=str(row["normalization_method"]),
            source=str(row["source"]),
            source_id=str(row["source_id"]),
            staleness_days=int(row["staleness_days"]),
            scenarios={str(key): float(value) for key, value in row.get("scenarios", {}).items()},
            lookback_days=int(row.get("lookback_days", 1825)),
            minimum_observations=int(row.get("minimum_observations", 60)),
            enabled=bool(row.get("enabled", True)),
            availability_note=(str(row["availability_note"]) if row.get("availability_note") else None),
        )
        if definition.id in seen:
            raise ValueError(f"Duplicate indicator id: {definition.id}")
        if not 1 <= definition.staleness_days <= 365:
            raise ValueError(f"Invalid staleness_days for {definition.id}")
        if not 30 <= definition.lookback_days <= 3650:
            raise ValueError(f"Invalid lookback_days for {definition.id}")
        if not 2 <= definition.minimum_observations <= 10000:
            raise ValueError(f"Invalid minimum_observations for {definition.id}")
        if any(weight < 0 for weight in definition.scenarios.values()):
            raise ValueError(f"Scenario weights cannot be negative for {definition.id}")
        seen.add(definition.id)
        definitions.append(definition)
    return definitions
