# parse_manual.py
import json
from pathlib import Path
from bs4 import BeautifulSoup

MANUAL_DIR = Path("manual/msp.ucsd.edu/Pd_documentation")
BASE_URL = "http://msp.ucsd.edu/Pd_documentation"


def parse_list(element):
    items = []
    for li in element.find_all("li", recursive=False):
        items.append("- " + li.get_text(" ", strip=True))
    return "\n".join(items)


def parse_file(filepath):
    with open(filepath, encoding="utf-8") as f:
        soup = BeautifulSoup(f.read(), "html.parser")

    filename = filepath.name

    # Remove top/bottom navigation bars
    for tag in soup.select("div.nav"):
        tag.decompose()

    # Chapter pages use div#corpus; index pages use div#toc (skip them)
    main = soup.select_one("div#corpus")
    if not main:
        return []

    sections = []
    current_headings = []
    current_blocks = []
    current_anchor = None

    def save_section():
        if current_blocks:
            url = f"{BASE_URL}/{filename}"
            if current_anchor:
                url += f"#{current_anchor}"
            sections.append({
                "heading_path": " > ".join(current_headings),
                "text": "\n".join(current_blocks).strip(),
                "url": url,
                "source": "msp_manual",
                "content_type": "conceptual",
                "object_name": None,
            })

    for element in main.children:
        if not hasattr(element, "name") or element.name is None:
            continue  # skip bare text nodes

        if element.name in ("h2", "h3", "h4"):
            save_section()
            current_blocks = []

            # H2 is the chapter level (no H1 in content), so map h2→0, h3→1, h4→2
            level = int(element.name[1]) - 2
            current_headings = current_headings[:level]

            a = element.find("a", id=True)
            current_anchor = a["id"] if a else None

            current_headings.append(element.get_text(" ", strip=True))

        elif element.name == "p":
            text = element.get_text(" ", strip=True)
            if text:
                current_blocks.append(text)

        elif element.name == "pre":
            current_blocks.append("[CODE]\n" + element.get_text() + "\n[/CODE]")

        elif element.name in ("ul", "ol"):
            current_blocks.append(parse_list(element))

        elif element.name == "figure":
            figcaption = element.find("figcaption")
            if figcaption:
                current_blocks.append("[Figure: " + figcaption.get_text(strip=True) + "]")

        elif element.name == "table":
            rows = []
            for row in element.find_all("tr"):
                cells = [cell.get_text(strip=True) for cell in row.find_all(["td", "th"])]
                rows.append(" | ".join(cells))
            if rows:
                current_blocks.append("\n".join(rows))

    save_section()
    return sections


all_sections = []
for filepath in sorted(MANUAL_DIR.glob("[0-9]*.htm")):
    sections = parse_file(filepath)
    all_sections.extend(sections)
    print(f"  {filepath.name}: {len(sections)} sections")

with open("parsed_manual.json", "w") as f:
    json.dump(all_sections, f, indent=2)

print(f"\nTotal: {len(all_sections)} sections")
