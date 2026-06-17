# parse_book.py
"""
Parse Puckette "The Theory and Techniques of Electronic Music" from the
local mirror at puckette_book/ into the same schema as parsed_manual.json.

Each section is a nodeN.html file. Content is bracketed by HTML comments:
  <!--End of Navigation Panel-->   ← content starts
  ...
  <!--Navigation Panel-->          ← content ends (bottom nav)
"""
import json
import re
from pathlib import Path
from bs4 import BeautifulSoup

BOOK_DIR = Path("puckette_book/msp.ucsd.edu/techniques/v0.08/book-html")
BASE_URL = "http://msp.ucsd.edu/techniques/v0.08/book-html"
TOC_FILE = "node1.html"

SKIP_FILES = {
    "node1.html",   # table of contents
    "node195.html", # index
    "book.html",
    "footnode.html",
    "index.html",
}


def build_node_map(toc_path):
    """Return {filename: title} from the TOC page."""
    soup = BeautifulSoup(toc_path.read_text(encoding="utf-8"), "html.parser")
    nodes = {}
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if re.match(r"^node\d+\.html$", href):
            text = " ".join(a.get_text().split())
            if text:
                nodes.setdefault(href, text)
    return nodes


def get_parent_file(soup):
    """Return the filename from link rel='up', or None."""
    for link in soup.find_all("link", rel=True):
        rel = link.get("rel", [])
        if isinstance(rel, list):
            rel = rel[0] if rel else ""
        if str(rel).lower() == "up":
            return link.get("href", "")
    return None


def parse_content(html_text):
    """
    Extract prose from a node page. Content lives between the
    first '<!--End of Navigation Panel-->' and the next
    '<!--Navigation Panel-->' comment (bottom nav). Math equations
    are <img> tags and are discarded.
    """
    match = re.search(
        r"<!--End of Navigation Panel-->(.*?)(?:<!--Navigation Panel-->|$)",
        html_text,
        re.DOTALL,
    )
    if not match:
        return ""

    soup = BeautifulSoup(match.group(1), "html.parser")

    blocks = []
    for el in soup.find_all(["p", "pre", "caption"]):
        for img in el.find_all("img"):
            img.replace_with("")

        if el.name == "pre":
            code = el.get_text().strip()
            if code:
                blocks.append(f"[CODE]\n{code}\n[/CODE]")

        elif el.name == "caption":
            text = " ".join(el.get_text().split())
            if len(text) > 15:
                blocks.append(f"[Figure: {text}]")

        else:
            text = " ".join(el.get_text().split())
            if len(text) < 30:
                continue
            nav_hits = sum(
                1 for w in ["Next:", "Up:", "Previous:", "Contents", "Index"]
                if w in text
            )
            if nav_hits >= 2:
                continue
            blocks.append(text)

    return "\n\n".join(blocks).strip()


node_map = build_node_map(BOOK_DIR / TOC_FILE)
print(f"TOC: {len(node_map)} nodes")

html_files = sorted(
    [f for f in BOOK_DIR.glob("node*.html") if f.name not in SKIP_FILES],
    key=lambda f: int(re.search(r"\d+", f.name).group()),
)

sections = []
for filepath in html_files:
    html_text = filepath.read_text(encoding="utf-8")
    soup = BeautifulSoup(html_text, "html.parser")

    title = soup.title.get_text(strip=True) if soup.title else node_map.get(filepath.name, "")
    parent_file = get_parent_file(soup)

    if parent_file and parent_file != "book.html" and parent_file in node_map:
        heading_path = f"{node_map[parent_file]} > {title}"
    else:
        heading_path = title

    text = parse_content(html_text)
    if not text:
        print(f"  {filepath.name}: (no content, skipped)")
        continue

    sections.append({
        "heading_path": heading_path,
        "text": text,
        "url": f"{BASE_URL}/{filepath.name}",
        "source": "puckette_book",
        "content_type": "conceptual",
        "object_name": None,
    })
    print(f"  {filepath.name}: {heading_path[:70]} — {len(text)} chars")

print(f"\nTotal: {len(sections)} sections")
with open("parsed_book.json", "w") as f:
    json.dump(sections, f, indent=2)
print("Wrote parsed_book.json")
