"""Extract dated H(cm) rows from INHGA's published forecast image.

The grid determines cell boundaries; OCR never infers values from neighbouring
cells. Dates, station identity, units and confidence are checked before use.
Only the small recognition model is loaded (no detector or angle classifier).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
import math
import re
import unicodedata

import cv2
import numpy as np
import onnxruntime as ort


_STATIONS = {
    "Calafat": "795", "Bechet": "679", "Corabia": "630",
    "Turnu Magurele": "597", "Zimnicea": "554", "Giurgiu": "493",
    "Oltenita": "430", "Calarasi": "370", "Fetesti": "",
    "Cernavoda": "300", "Harsova": "253", "Vadu Oii": "",
    "Braila": "170", "Galati": "150", "Grindu": "",
    "Isaccea": "103", "Tulcea": "71",
}


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z]", "", text.lower())


def _station_name(text: str, confidence: float) -> str | None:
    if confidence < 0.85:
        return None
    key = _normalize(text)
    aliases = {_normalize(name): name for name in _STATIONS}
    aliases["trmagurele"] = "Turnu Magurele"
    if key in aliases:
        return aliases[key]
    scores = sorted((SequenceMatcher(None, key, alias).ratio(), name)
                    for alias, name in aliases.items())
    # Permit a single OCR spelling error only when the match is unambiguous.
    if scores[-1][0] >= 0.85 and scores[-1][0] - scores[-2][0] >= 0.15:
        return scores[-1][1]
    return None


@lru_cache(maxsize=1)
def _recognizer():
    options = ort.SessionOptions()
    options.intra_op_num_threads = 1
    options.inter_op_num_threads = 1
    options.enable_cpu_mem_arena = False
    session = ort.InferenceSession(
        str(Path(__file__).parent / "models" / "ch_PP-OCRv4_rec_infer.onnx"),
        sess_options=options, providers=["CPUExecutionProvider"],
    )
    alphabet = [""] + session.get_modelmeta().custom_metadata_map["character"].splitlines() + [" "]
    return session, alphabet


def _recognize(crops: list[np.ndarray]) -> list[tuple[str, float]]:
    """PP-OCR recognition preprocessing and standard CTC decoding."""
    session, alphabet = _recognizer()
    results = [("", 0.0)] * len(crops)
    order = sorted(range(len(crops)), key=lambda i: crops[i].shape[1] / crops[i].shape[0])
    for start in range(0, len(order), 6):
        indices = order[start:start + 6]
        width = max(320, max(math.ceil(48 * crops[i].shape[1] / crops[i].shape[0]) for i in indices))
        batch = np.zeros((len(indices), 3, 48, width), dtype=np.float32)
        for j, i in enumerate(indices):
            crop = crops[i]
            resized_width = math.ceil(48 * crop.shape[1] / crop.shape[0])
            resized = cv2.resize(crop, (resized_width, 48)).astype(np.float32)
            batch[j, :, :, :resized_width] = resized.transpose(2, 0, 1) / 127.5 - 1
        probabilities = session.run(None, {session.get_inputs()[0].name: batch})[0]
        for i, prob in zip(indices, probabilities):
            tokens = prob.argmax(axis=1)
            keep = np.r_[True, tokens[1:] != tokens[:-1]] & (tokens != 0)
            text = "".join(alphabet[token] for token in tokens[keep])
            score = float(prob.max(axis=1)[keep].mean()) if keep.any() else 0.0
            results[i] = (text, score)
    return results


def _groups(mask: np.ndarray) -> list[np.ndarray]:
    indices = np.flatnonzero(mask)
    return [group for group in np.split(indices, np.where(np.diff(indices) > 2)[0] + 1) if len(group)]


def _clean_cell(image: np.ndarray) -> np.ndarray:
    ink = (cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) < 145).astype(np.uint8)
    # Remove the table rules and JPEG speckles, retaining minus signs.
    ink[ink.sum(axis=1) > ink.shape[1] * 0.5] = 0
    count, labels, stats, _ = cv2.connectedComponentsWithStats(ink)
    for i in range(1, count):
        if stats[i, cv2.CC_STAT_AREA] < 4:
            ink[labels == i] = 0
    points = cv2.findNonZero(ink)
    if points is not None:
        x, y, w, h = cv2.boundingRect(points)
        ink = ink[y:y + h, x:x + w]
    crop = cv2.cvtColor(255 - ink * 255, cv2.COLOR_GRAY2BGR)
    return cv2.copyMakeBorder(crop, 3, 3, 3, 3, cv2.BORDER_CONSTANT, value=(255, 255, 255))


def _read_grid(content: bytes) -> list[list[tuple[str, float]]]:
    image = cv2.imdecode(np.frombuffer(content, np.uint8), cv2.IMREAD_COLOR)
    if image is None or not (700 <= image.shape[1] <= 4000 and image.shape[0] <= 6000):
        raise ValueError("INHGA forecast image is invalid or too small to read reliably")
    ink = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) < 160
    ys = [int(group.mean()) for group in _groups(ink.sum(axis=1) > image.shape[1] * 0.75)]
    xs = [int(group.mean()) for group in _groups(ink.sum(axis=0) > image.shape[0] * 0.7)]
    if not (9 <= len(xs) <= 12 and 12 <= len(ys) <= 30):
        raise ValueError("Unrecognized INHGA forecast grid")
    for x in xs:
        ink[:, max(0, x - 2):x + 3] = False
    crops = []
    for top, bottom in zip(ys, ys[1:]):
        lines = _groups(ink[top + 3:bottom - 2, xs[2] + 3:xs[-1] - 3].sum(axis=1) > 2)
        if not lines:
            continue
        # Each station block has H, Q and other lines; read only its first line.
        end = min(bottom - 1, int(lines[0][-1]) + top + 6)
        for left, right in zip(xs, xs[1:]):
            crops.append(_clean_cell(image[top + 1:end, left + 4:right - 3]))
    recognized = _recognize(crops)
    size = len(xs) - 1
    return [recognized[i:i + size] for i in range(0, len(recognized), size)]


def _parse_grid(grid: list[list[tuple[str, float]]], bulletin_date: str) -> list[dict]:
    if not grid:
        raise ValueError("Empty INHGA forecast grid")
    issued = datetime.strptime(bulletin_date, "%d.%m.%Y").date()
    dates = []
    for offset, (text, confidence) in enumerate(grid[0][2:]):
        # Some image encodings lose the period glyphs; all date digits must match.
        digits = re.sub(r"[.\s]", "", text)
        expected = issued + timedelta(days=offset)
        if confidence < 0.9 or digits != expected.strftime("%d%m%Y"):
            raise ValueError("INHGA forecast dates could not be verified against the bulletin")
        dates.append(expected)
    if len(dates) < 6:
        raise ValueError("Incomplete INHGA forecast date header")
    records = []
    seen = set()
    for cells in grid[1:]:
        if len(cells) != len(dates) + 2:
            continue
        # Baziaș and P.D.F. II contain Q(m³/s), not water levels; never mix units.
        unit, unit_score = cells[1]
        if _normalize(unit) != "hcm" or unit_score < 0.8:
            continue
        station = _station_name(*cells[0])
        if station is None or station in seen:
            continue
        values = []
        for text, confidence in cells[2:]:
            text = text.strip().replace("−", "-")
            valid = confidence >= 0.92 and re.fullmatch(r"-?\d{1,4}", text)
            values.append(str(int(text)) if valid and -1000 <= int(text) <= 10000 else "")
        if not values[0] or sum(v != "" for v in values[1:]) < len(dates) - 2:
            continue
        seen.add(station)
        records.append({
            "station": station, "km": _STATIONS[station], "level": values[0],
            "change": "", "observed_date": issued, "forecast_date": issued,
            "forecasts": values[1:],
        })
    if len(records) < 10:
        raise ValueError("Too few INHGA station forecasts could be read reliably")
    return records


def parse_bulletin_image(content: bytes, bulletin_date: str) -> list[dict]:
    return _parse_grid(_read_grid(content), bulletin_date)
