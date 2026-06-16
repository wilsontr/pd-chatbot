# parse_object_reference.py
import json
from pathlib import Path
from bs4 import BeautifulSoup

OBJ_REF_DIR = Path("object_reference/pd.iem.sh/objects")
BASE_URL = "https://pd.iem.sh/objects"


def get_source_url(filepath):
    parts = filepath.parts
    obj_idx = parts.index("objects")
    rel = parts[obj_idx + 1:]
    if len(rel) == 1:
        return f"{BASE_URL}/{rel[0].replace('.html', '')}"
    else:
        return f"{BASE_URL}/{rel[0]}/"


def parse_iolet_list(div):
    """Parse the nested ul/li iolet structure into 'slot | type | type' rows."""
    rows = []
    ul = div.find("ul")
    if not ul:
        return rows
    current_slot = None
    slot_types = []
    for child in ul.children:
        if not hasattr(child, "name") or child.name is None:
            continue
        if child.name == "li":
            if current_slot is not None:
                suffix = " | ".join(slot_types)
                rows.append(f"{current_slot} | {suffix}" if suffix else current_slot)
            current_slot = child.get_text(strip=True)
            slot_types = []
        elif child.name == "ul":
            for li in child.find_all("li", recursive=False):
                t = li.get_text(" ", strip=True)
                if t:
                    slot_types.append(t)
    if current_slot is not None:
        suffix = " | ".join(slot_types)
        rows.append(f"{current_slot} | {suffix}" if suffix else current_slot)
    return rows


def parse_list(element):
    items = []
    for li in element.find_all("li", recursive=False):
        items.append("- " + li.get_text(" ", strip=True))
    return "\n".join(items)


def parse_table(element):
    rows = []
    for row in element.find_all("tr"):
        cells = [cell.get_text(strip=True) for cell in row.find_all(["td", "th"])]
        rows.append(" | ".join(cells))
    return "\n".join(rows)


def parse_file(filepath):
    with open(filepath, encoding="utf-8") as f:
        soup = BeautifulSoup(f.read(), "html.parser")

    url = get_source_url(filepath)

    for tag in soup.select("header, footer"):
        tag.decompose()

    main = soup.select_one("main")
    if not main:
        return []

    # Object name — skip non-object pages (e.g. index listing)
    obj_name_tag = main.find("h3", class_="pdobj")
    if not obj_name_tag:
        return []
    object_name = obj_name_tag.get_text(strip=True)

    # Short description
    description = ""
    for p in main.find_all("p", recursive=False):
        if "105%" in p.get("style", ""):
            description = p.get_text(strip=True)
            break

    # Abbreviation line, e.g. "Abbreviation: t"
    abbreviation = ""
    for p in main.find_all("p", recursive=False):
        if p.get_text(" ", strip=True).startswith("Abbreviation:"):
            abbreviation = p.get_text(" ", strip=True)
            break

    # Inlets: try table rows first (some pages), fall back to ul/li structure
    inlet_rows = []
    for row in soup.select(".inlets tr, #inlets tr"):
        cells = [td.get_text().strip() for td in row.select("td")]
        if cells:
            inlet_rows.append(" | ".join(cells))
    if not inlet_rows:
        inlets_div = main.find("div", id="inlets")
        if inlets_div:
            inlet_rows = parse_iolet_list(inlets_div)

    # Outlets
    outlet_rows = []
    for row in soup.select(".outlets tr, #outlets tr"):
        cells = [td.get_text().strip() for td in row.select("td")]
        if cells:
            outlet_rows.append(" | ".join(cells))
    if not outlet_rows:
        outlets_div = main.find("div", id="outlets")
        if outlets_div:
            outlet_rows = parse_iolet_list(outlets_div)

    # Arguments / creation arguments
    arg_rows = []
    for row in soup.select(".arguments tr, #arguments tr"):
        cells = [td.get_text().strip() for td in row.select("td")]
        if cells:
            arg_rows.append(" | ".join(cells))
    if not arg_rows:
        args_div = main.find("div", id="arguments")
        if args_div:
            for li in args_div.find_all("li"):
                t = li.get_text(" ", strip=True)
                if t:
                    arg_rows.append(t)

    # See also
    seealso_div = main.find("div", id="reference-seealso")
    seealso = []
    if seealso_div:
        seealso = [a.get_text(strip=True) for a in seealso_div.find_all("a", class_="pdobj")]

    # Build structured text optimized for embedding
    text_parts = [f"Object: {object_name}", f"Description: {description}"]
    if abbreviation:
        text_parts.append(abbreviation)
    if inlet_rows:
        text_parts.append("Inlets:\n" + "\n".join(inlet_rows))
    if outlet_rows:
        text_parts.append("Outlets:\n" + "\n".join(outlet_rows))
    if arg_rows:
        text_parts.append("Arguments:\n" + "\n".join(arg_rows))
    if seealso:
        text_parts.append("See also: " + ", ".join(seealso))

    # Mark pre-processed elements to avoid repeating them below
    processed = {id(obj_name_tag)}
    for p in main.find_all("p", recursive=False):
        if "105%" in p.get("style", ""):
            processed.add(id(p))
            break
    for p in main.find_all("p", recursive=False):
        if p.get_text(" ", strip=True).startswith("Abbreviation:"):
            processed.add(id(p))
            break
    ref_div = main.find("div", class_="object-reference")
    if ref_div:
        processed.add(id(ref_div))

    # Additional narrative paragraphs, tables, and sub-section content
    extra_parts = []
    for element in main.children:
        if not hasattr(element, "name") or element.name is None:
            continue
        if id(element) in processed:
            continue
        if element.name == "h3":
            extra_parts.append("\n" + element.get_text(" ", strip=True))
        elif element.name == "h4":
            extra_parts.append("#### " + element.get_text(" ", strip=True))
        elif element.name == "p":
            t = element.get_text(" ", strip=True)
            if t:
                extra_parts.append(t)
        elif element.name == "pre":
            extra_parts.append("[CODE]\n" + element.get_text() + "\n[/CODE]")
        elif element.name in ("ul", "ol"):
            extra_parts.append(parse_list(element))
        elif element.name == "table":
            extra_parts.append(parse_table(element))

    if extra_parts:
        text_parts.append("\n".join(extra_parts))

    return [{
        "heading_path": object_name,
        "text": "\n\n".join(text_parts),
        "url": url,
        "source": "iem_reference",
        "content_type": "object_reference",
        "object_name": object_name,
    }]


all_records = []

# Direct .html files in objects/ (e.g. expr.html); skip index.html and any
# that also have a same-named subdirectory (the subdir version is canonical)
for filepath in sorted(OBJ_REF_DIR.glob("*.html")):
    if filepath.name == "index.html":
        continue
    if (OBJ_REF_DIR / filepath.stem / "index.html").exists():
        continue
    records = parse_file(filepath)
    all_records.extend(records)

# Subdirectory pages: objects/{name}/index.html
for filepath in sorted(OBJ_REF_DIR.glob("*/index.html")):
    records = parse_file(filepath)
    all_records.extend(records)

with open("parsed_object_reference.json", "w") as f:
    json.dump(all_records, f, indent=2)

print(f"Total: {len(all_records)} records from {len(list(OBJ_REF_DIR.glob('*/index.html')))} object pages")
