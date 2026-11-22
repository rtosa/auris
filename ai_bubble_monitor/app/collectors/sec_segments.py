"""Small, filing-specific inline XBRL extractor for a validated SEC prototype."""

from __future__ import annotations

from datetime import date
from html.parser import HTMLParser
from typing import Any

import yaml
from pathlib import Path

METRICS_FILE = Path(__file__).resolve().parents[2] / "config" / "filing_metrics.yaml"


def load_filing_configuration() -> dict[str, Any]:
    payload = yaml.safe_load(METRICS_FILE.read_text(encoding="utf-8")) or {}
    return payload if isinstance(payload, dict) else {}


def load_filing_metrics() -> dict[str, list[dict[str, Any]]]:
    payload = load_filing_configuration()
    return {str(key): list(value) for key, value in payload.get("companies", {}).items()}


class _InlineXbrlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.contexts: dict[str, dict[str, Any]] = {}
        self.facts: list[dict[str, Any]] = []
        self._context: dict[str, Any] | None = None
        self._context_id: str | None = None
        self._capture: tuple[str, str | None] | None = None
        self._capture_text: list[str] = []
        self._fact: dict[str, Any] | None = None
        self._fact_text: list[str] = []

    @staticmethod
    def _local(tag: str) -> str:
        return tag.rsplit(":", 1)[-1].lower()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {str(key).lower(): value for key, value in attrs}
        local = self._local(tag)
        if local == "context":
            self._context_id = str(attributes.get("id") or "")
            self._context = {"dimensions": {}}
        elif self._context is not None and local in {"instant", "startdate", "enddate"}:
            self._capture = (local, None)
            self._capture_text = []
        elif self._context is not None and local in {"explicitmember", "typedmember"}:
            self._capture = ("dimension", str(attributes.get("dimension") or ""))
            self._capture_text = []
        if local == "nonfraction":
            self._fact = {
                "tag": str(attributes.get("name") or ""),
                "context_ref": str(attributes.get("contextref") or ""),
                "unit_ref": str(attributes.get("unitref") or ""),
                "scale": attributes.get("scale") or "0",
                "sign": attributes.get("sign"),
            }
            self._fact_text = []

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if self._capture is not None:
            self._capture_text.append(data)
        if self._fact is not None:
            self._fact_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        local = self._local(tag)
        if local == "nonfraction" and self._fact is not None:
            self._fact["raw_value"] = "".join(self._fact_text).strip()
            self.facts.append(self._fact)
            self._fact = None
            self._fact_text = []
        if self._capture is not None:
            capture_type, dimension = self._capture
            value = "".join(self._capture_text).strip()
            if self._context is not None and value:
                if capture_type == "dimension" and dimension:
                    self._context["dimensions"][dimension] = value
                elif capture_type in {"instant", "startdate", "enddate"}:
                    self._context[capture_type] = value
            if local in {"instant", "startdate", "enddate", "explicitmember", "typedmember"}:
                self._capture = None
                self._capture_text = []
        if local == "context" and self._context is not None:
            self.contexts[self._context_id or ""] = self._context
            self._context = None
            self._context_id = None


def extract_filing_metrics(
    document: str,
    company: dict[str, str],
    filing: dict[str, Any],
    definitions: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Extract only configured, dimension-matched facts from one inline XBRL filing."""
    if definitions is None:
        definitions = load_filing_metrics().get(company["id"], [])
    if not definitions:
        return []
    parser = _InlineXbrlParser()
    parser.feed(document)
    parser.close()
    output: list[dict[str, Any]] = []

    for definition in definitions:
        matches: list[tuple[date, dict[str, Any], dict[str, Any]]] = []
        required = definition.get("required_dimensions", {})
        required_values = definition.get("required_dimension_values", {})
        forbidden = set(definition.get("forbidden_dimensions", []))
        for fact in parser.facts:
            if fact["tag"] != definition["tag"]:
                continue
            context = parser.contexts.get(fact["context_ref"])
            if not context:
                continue
            dimensions = context["dimensions"]
            if any(dimensions.get(key) != value for key, value in required.items()):
                continue
            if any(dimensions.get(key) not in values for key, values in required_values.items()):
                continue
            if forbidden.intersection(dimensions):
                continue
            required_axes = set(definition.get("required_dimension_axes", []))
            required_dimension_axes = set(required) | set(required_values) | required_axes
            if any(key not in dimensions for key in required_axes):
                continue
            if definition.get("exact_dimensions", True) and set(dimensions) != required_dimension_axes:
                continue
            end_text = context.get("instant") or context.get("enddate")
            if not end_text:
                continue
            end = date.fromisoformat(end_text)
            start_text = context.get("startdate")
            period_kind = definition.get("period_kind", "duration")
            if period_kind == "instant" and not context.get("instant"):
                continue
            if period_kind == "quarter" and (not start_text or not 70 <= (end - date.fromisoformat(start_text)).days + 1 <= 110):
                continue
            try:
                numeric = float(fact["raw_value"].replace(",", "").replace("−", "-").replace(" ", ""))
                numeric *= 10 ** int(fact["scale"] or 0)
            except (ValueError, TypeError):
                continue
            if fact.get("sign") == "-":
                numeric = -abs(numeric)
            matches.append((end, {**fact, "value": numeric}, context))

        if not matches:
            continue
        if not definition.get("exact_dimensions", True):
            minimum_extra_dimensions = min(
                len(context["dimensions"]) - len(set(required) | set(required_values) | set(definition.get("required_dimension_axes", [])))
                for _, _, context in matches
            )
            matches = [item for item in matches
                       if len(item[2]["dimensions"]) - len(set(required) | set(required_values) | set(definition.get("required_dimension_axes", [])))
                       == minimum_extra_dimensions]
        latest_end = max(item[0] for item in matches)
        selected_ends = {latest_end}
        if definition.get("include_year_ago_comparative"):
            year_ago = [item[0] for item in matches
                        if item[0] < latest_end and 320 <= (latest_end - item[0]).days <= 400]
            if year_ago:
                selected_ends.add(max(year_ago))

        for selected_end in sorted(selected_ends):
            same_period = [item for item in matches if item[0] == selected_end]
            # Filers can repeat the same fact in multiple tables. Collapse identical
            # tagged values while keeping distinct same-period values ambiguous.
            unique_period: dict[tuple[Any, ...], tuple[date, dict[str, Any], dict[str, Any]]] = {}
            for item in same_period:
                _, fact, context = item
                key = (fact["value"], fact["unit_ref"], context.get("startdate"), context.get("instant"),
                       context.get("enddate"), tuple(sorted(context["dimensions"].items())))
                unique_period[key] = item
            same_period = list(unique_period.values())
            if len(same_period) != 1:
                raise ValueError(f"Ambiguous XBRL facts for {definition['metric']} in {filing['accession']}")
            end, fact, context = same_period[0]
            start = context.get("startdate")
            output.append({
                "metric": definition["metric"],
                "value": fact["value"],
                "unit": "USD" if fact["unit_ref"].lower() == "usd" else fact["unit_ref"],
                "period": end.isoformat(),
                "start": start,
                "end": end.isoformat(),
                "filed": filing["filed"],
                "accession": filing["accession"],
                "form": filing["form"],
                "fy": None,
                "fp": None,
                "tag": fact["tag"],
                "source_url": filing["source_url"],
            })
    return output


def extract_customer_concentration_metrics(
    document: str,
    company: dict[str, str],
    filing: dict[str, Any],
    definition: dict[str, Any],
) -> list[dict[str, Any]]:
    """Extract the highest explicitly reported customer share of revenue."""
    parser = _InlineXbrlParser()
    parser.feed(document)
    parser.close()
    tags = set(definition.get("tags", []))
    required = definition.get("required_dimensions", {})
    allowed = definition.get("allowed_dimension_values", {})
    customer_axes = set(definition.get("customer_axes", []))
    benchmark_axis = definition.get("benchmark_axis")
    benchmark_members = set(definition.get("benchmark_members", []))
    matches: list[tuple[date, float, dict[str, Any], dict[str, Any]]] = []

    for fact in parser.facts:
        if fact["tag"] not in tags:
            continue
        context = parser.contexts.get(fact["context_ref"])
        if not context:
            continue
        dimensions = context["dimensions"]
        if not any(axis in dimensions for axis in customer_axes):
            continue
        if any(dimensions.get(axis) != value for axis, value in required.items()):
            continue
        if any(dimensions.get(axis) not in values for axis, values in allowed.items()):
            continue
        if benchmark_axis and dimensions.get(benchmark_axis) not in benchmark_members:
            continue
        end_text = context.get("instant") or context.get("enddate")
        if not end_text or (definition.get("period_kind") == "instant" and not context.get("instant")):
            continue
        try:
            value = float(fact["raw_value"].replace(",", "").replace("−", "-").replace(" ", ""))
            value *= 10 ** int(fact["scale"] or 0)
        except (ValueError, TypeError):
            continue
        if fact.get("sign") == "-":
            value = -abs(value)
        # XBRL percent concepts use a unitless ratio (e.g. 0.33 for 33%).
        value *= 100
        if not 0 <= value <= 100:
            continue
        matches.append((date.fromisoformat(end_text), value, fact, context))

    if not matches:
        return []
    by_period: dict[date, tuple[date, float, dict[str, Any], dict[str, Any]]] = {}
    for match in matches:
        end = match[0]
        if end not in by_period or match[1] > by_period[end][1]:
            by_period[end] = match
    selected_dates = {max(by_period)}
    if definition.get("include_year_ago_comparative"):
        latest = max(by_period)
        prior = [period for period in by_period if period < latest and 320 <= (latest - period).days <= 400]
        if prior:
            selected_dates.add(max(prior))

    output = []
    for period in sorted(selected_dates):
        end, value, fact, context = by_period[period]
        output.append({
            "metric": definition.get("metric", "customer_revenue_concentration_percent"),
            "value": value,
            "unit": "percent",
            "period": end.isoformat(),
            "start": context.get("startdate"),
            "end": end.isoformat(),
            "filed": filing["filed"],
            "accession": filing["accession"],
            "form": filing["form"],
            "fy": None,
            "fp": None,
            "tag": fact["tag"],
            "source_url": filing["source_url"],
        })
    return output
