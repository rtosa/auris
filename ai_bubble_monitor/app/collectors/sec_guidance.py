"""Conservative extraction of numeric capex guidance from SEC filing exhibits."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any


class _VisibleText(HTMLParser):
    _BLOCKS = {"br", "p", "div", "li", "tr", "td", "th", "h1", "h2", "h3"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden_tags: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "ix:header"}:
            self.hidden_tags.append(tag.lower())
        if tag.lower() in self._BLOCKS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self.hidden_tags:
            self.hidden_tags.remove(tag.lower())
        if tag.lower() in self._BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.hidden_tags:
            self.parts.append(data)


_AMOUNT = r"\$?\s*(\d[\d,]*(?:\.\d+)?)(?:\s*(trillion|tn|t|billion|bn|b|million|mn|m)\b)?"
_SCALE = {"trillion": 1_000_000_000_000, "tn": 1_000_000_000_000, "t": 1_000_000_000_000,
          "billion": 1_000_000_000, "bn": 1_000_000_000, "b": 1_000_000_000,
          "million": 1_000_000, "mn": 1_000_000, "m": 1_000_000}
_RANGE = re.compile(rf"(?:(?:between|from|in\s+the\s+range\s+of|range\s+of)\s+)?{_AMOUNT}\s*(?:and|to|through|[-–])\s*{_AMOUNT}", re.I)
_SINGLE = re.compile(_AMOUNT, re.I)
_CAPEX = re.compile(r"\b(?:capital\s+expenditures?|capital\s+spending|capex)\b", re.I)
_FORWARD = re.compile(r"\b(?:expect(?:s|ed|ing)?|anticipat(?:e|es|ed|ing)|forecast(?:s|ed)?|guidance|outlook|plan(?:s|ned)?\s+to|budget(?:s|ed)?|project(?:s|ed)?|will\s+(?:spend|invest))\b", re.I)
_NEGATED = re.compile(r"\b(?:do(?:es)?\s+not|did\s+not|not\s+expected\s+to|no\s+longer\s+expect)\b", re.I)
_OTHER_METRIC = re.compile(r"\b(?:free\s+cash\s+flow|FCF|revenue|EBITDA|operating\s+income|net\s+income)\b", re.I)


def _usd(amount: str, scale: str | None) -> float | None:
    if not scale:
        return None
    try:
        return float(amount.replace(",", "")) * _SCALE[scale.lower()]
    except (ValueError, KeyError):
        return None


def extract_capex_guidance(
    document: str,
    company: dict[str, str],
    filing: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return only explicit future capex figures with a quoted filing sentence.

    The first version intentionally accepts numeric points and ranges with an
    explicit USD scale. It does not infer units, normalize qualitative wording,
    or treat realized CAPEX as guidance.
    """
    parser = _VisibleText()
    parser.feed(document)
    parser.close()
    text = re.sub(r"[\t\xa0 ]+", " ", "".join(parser.parts))
    sentences = [re.sub(r"\s+", " ", part).strip(" \n•–-") for part in re.split(r"(?<=[.!?;])\s+|\n+", text)]
    for sentence in sentences:
        if not sentence or len(sentence) > 700 or not _CAPEX.search(sentence) or not _FORWARD.search(sentence) or _NEGATED.search(sentence):
            continue
        range_match = _RANGE.search(sentence)
        lower = upper = None
        capex_match = _CAPEX.search(sentence)
        assert capex_match is not None
        if range_match and not _OTHER_METRIC.search(sentence, capex_match.end(), range_match.start()):
            first_amount, first_scale, second_amount, second_scale = range_match.groups()
            scale = second_scale or first_scale
            lower = _usd(first_amount, first_scale or scale)
            upper = _usd(second_amount, second_scale or scale)
        if lower is None or upper is None:
            for match in _SINGLE.finditer(sentence):
                amount, scale = match.groups()
                # Ignore unscaled years and ordinary numbers; no unit is inferred.
                if scale and not _OTHER_METRIC.search(sentence, capex_match.end(), match.start()):
                    lower = upper = _usd(amount, scale)
                    break
        if lower is None or upper is None or lower < 0 or upper < lower:
            continue
        years = list(re.finditer(r"\b(?:FY|fiscal\s+year|calendar\s+year)?\s*(20\d{2})\b", sentence, re.I))
        capex_start = capex_match.start()
        year = min(years, key=lambda match: abs(match.start() - capex_start)).group(1) if years else None
        return [{
            "title": f"{company['ticker']} {year + ' ' if year else ''}capital expenditure guidance",
            "summary": sentence,
            "company_id": company["id"],
            "category": "capex_guidance",
            "published_at": filing["filed"],
            "source_url": filing["source_url"],
            "confidence": 0.85,
            "guidance_period": year,
            "guidance_lower": lower,
            "guidance_upper": upper,
            "guidance_unit": "USD",
            "source_quote": sentence,
            "source_accession": filing["accession"],
        }]
    return []
