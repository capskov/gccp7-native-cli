"""Material search terms and fallback ranking for quotation rows."""
from difflib import SequenceMatcher
import re
import unicodedata

MATERIALS = (
    "闸阀", "闸门", "弯头", "伸缩器", "伸缩节", "补偿器", "伸缩接头",
    "蝶阀", "球阀", "止回阀", "截止阀", "减压阀", "排气阀", "安全阀",
    "调节阀", "电磁阀", "三通", "四通", "异径管", "大小头", "法兰",
    "直通", "接头", "水表", "水泵", "钢管", "铸铁管", "塑料管",
)
ALIASES = {"伸缩节": "伸缩器", "伸缩接头": "伸缩器", "大小头": "异径管"}
MATERIAL = re.compile("|".join(sorted(MATERIALS, key=len, reverse=True)))
DIAMETER = re.compile(r"(?:(?<![a-z0-9])dn\s*|[φΦø⌀]\s*|公称直径(?:\s*\(mm\))?\s*[:：]?\s*)(\d{1,5})", re.I)
MODEL = re.compile(r"(?<![a-z0-9])[a-z]{1,5}\d[a-z0-9]*(?:-[a-z0-9]+)+(?![a-z0-9])", re.I)
CHINESE = re.compile(r"[\u3400-\u9fff]+")


def normalize(text):
    return unicodedata.normalize("NFKC", str(text or "")).casefold()


def material_type(text):
    match = MATERIAL.search(text)
    return ALIASES.get(match[0], match[0]) if match else ""


def diameter(text):
    match = DIAMETER.search(text)
    return int(match[1]) if match else None


def keyword_filter(name, specification=""):
    text = normalize(f"{name} {specification}")
    kind = material_type(text)
    if not kind:
        return name.strip() or specification.strip()
    size = diameter(text)
    if size is None:
        size = next((int(word) for word in text.split() if word.isdecimal()), None)
    return f"{kind} {size}" if size else kind


def rank_quotes(rows, query, reference="", core_only=False):
    text = normalize(query)
    kind = material_type(text)
    size = diameter(text)
    if kind and size is None:
        size = next((int(word) for word in text.split() if word.isdecimal()), None)
    description = normalize(reference or query)
    model = MODEL.search(description)
    qualifiers = {part[i:i + 2] for part in CHINESE.findall(description) for i in range(len(part) - 1)}
    words = {word for word in text.split() if len(word) > 1 and not word.isdecimal()}
    fragments = {word[i:i + 2] for word in CHINESE.findall(text) for i in range(len(word) - 1)}
    ranked = []
    for row in rows:
        name = normalize(row["material"])
        category_kind = material_type(normalize(row.get("category")))
        row_kind = material_type(name) or category_kind
        spec = normalize(f"{row['model']} {row['specification']}")
        row_size = row["dn"] or diameter(spec)
        if kind and kind != row_kind and (core_only or kind != category_kind):
            continue
        if core_only and size is not None and row_size != size:
            continue
        exact_model = bool(model and model[0] in spec)
        if kind:
            distance = abs(size - row_size) if size and row_size else (100000 if size else 0)
            row_model = MODEL.search(spec)
            model_similarity = (SequenceMatcher(None, model[0], row_model[0]).ratio()
                                if model and row_model else 0)
            qualifier_hits = sum(part in name or part in spec for part in qualifiers)
            score = (kind == row_kind, bool(size and size == row_size), exact_model,
                     -distance, qualifier_hits, model_similarity)
        else:
            word_hits = sum(word in name or word in spec for word in words)
            fragment_hits = sum(fragment in name for fragment in fragments)
            if not (word_hits or exact_model or fragment_hits >= max(2, (len(fragments) + 2) // 3)):
                continue
            score = (exact_model, word_hits, fragment_hits)
        ranked.append((score, row))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [row for _, row in ranked]
