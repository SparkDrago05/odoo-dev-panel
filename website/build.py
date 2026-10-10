#!/usr/bin/env python3
"""Build the project website into a folder for GitHub Pages.

    python3 website/build.py OUT [--version 0.13.0]

OUT gets index.html, docs/*.html (rendered from the repository's Markdown), style.css and assets/.
The APT repository is built separately into OUT/apt (packaging/apt/build-repo.sh).
Needs the `markdown` package.
"""

import argparse
import html
import re
import shutil
from pathlib import Path

import markdown

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
GITHUB = "https://github.com/SparkDrago05/odoo-dev-panel"

# (source, output name, title in the docs navigation)
DOCS = [
    ("docs/install.md", "install", "Install and first run"),
    ("docs/troubleshooting.md", "troubleshooting", "Troubleshooting"),
    ("SECURITY.md", "security", "Security"),
    ("CONTRIBUTING.md", "contributing", "Contributing"),
]

# Markdown links between the documents become links between the pages.
LINKS = {src.rsplit("/", 1)[-1]: f"{name}.html" for src, name, _ in DOCS}
LINKS["README.md"] = "../index.html"
CURRENT = ' aria-current="page"'


def rewrite_link(match: re.Match, src_dir: Path) -> str:
    target = match.group(2)
    if re.match(r"^[a-z]+:|^#", target):
        return match.group(0)
    path, _, anchor = target.partition("#")
    page = LINKS.get(path.rsplit("/", 1)[-1])
    if page:
        url = page + (f"#{anchor}" if anchor else "")
    else:
        # Anything else (LICENSE, source files) is shown on GitHub.
        rel = (src_dir / path).resolve().relative_to(REPO)
        url = f"{GITHUB}/blob/main/{rel.as_posix()}" + (f"#{anchor}" if anchor else "")
    return f"{match.group(1)}({url})"


def render(src: Path) -> tuple[str, str]:
    md_text = re.sub(r"(\]\s?)\(([^)\s]+)\)", lambda m: rewrite_link(m, src.parent), src.read_text())
    first = md_text.splitlines()[0]
    title = first.lstrip("# ").strip() if first.startswith("# ") else ""
    body = markdown.markdown(
        md_text,
        extensions=["tables", "fenced_code", "toc", "sane_lists"],
        extension_configs={"toc": {"permalink": "#", "permalink_class": "anchor"}},
        output_format="html",
    )
    return title, body


def page(template: str, **values: str) -> str:
    # One pass over the template, so values (rendered docs) may contain "{{...}}" themselves.
    def fill(match: re.Match) -> str:
        if match.group(1) not in values:
            raise SystemExit(f"unfilled placeholder: {match.group(0)}")
        return values[match.group(1)]

    return re.sub(r"\{\{(\w+)\}\}", fill, template)


def build(out: Path, version: str) -> None:
    if out.exists():
        shutil.rmtree(out)
    (out / "docs").mkdir(parents=True)
    shutil.copytree(HERE / "assets", out / "assets")
    shutil.copy(HERE / "style.css", out / "style.css")
    (out / ".nojekyll").write_text("")

    index = (HERE / "index.html").read_text()
    (out / "index.html").write_text(page(index, version=version))

    layout = (HERE / "doc.html").read_text()
    for src, name, nav_title in DOCS:
        title, body = render(REPO / src)
        nav = "\n".join(
            f'<a href="{n}.html"{CURRENT if n == name else ""}>{html.escape(t)}</a>'
            for _, n, t in DOCS
        )
        (out / "docs" / f"{name}.html").write_text(
            page(
                layout,
                title=html.escape(title or nav_title),
                nav=nav,
                body=body,
                source=f"{GITHUB}/blob/main/{src}",
                version=version,
            )
        )
    print(f"site in {out}: index.html, {len(DOCS)} docs, version {version}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out", type=Path)
    parser.add_argument("--version", default="")
    args = parser.parse_args()
    version = args.version.lstrip("v") or re.search(
        r'"version": "([^"]+)"', (REPO / "app/src-tauri/tauri.conf.json").read_text()
    ).group(1)
    build(args.out, version)


if __name__ == "__main__":
    main()
