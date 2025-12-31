"""Utils to convert a Markdown text to another format."""
__all__ = [
    "to_html", "to_pdf", "load_fonts_to_css", "FontMap",
    "DEFAULT_LAYOUT_CSS", "DEFAULT_FONT_CSS", "HTML_TEMPLATE"
]

from logging import getLogger
from os.path import abspath, commonpath, dirname, isdir, join as path_join
from urllib.parse import urlparse

import yaml
from bs4 import BeautifulSoup
from markdown.core import markdown as _md2html_body
from pydantic import BaseModel, model_validator
from pygments.formatters import HtmlFormatter


class FontMap(BaseModel):
    class Rule(BaseModel):
        filter: str
        fonts: list[str]

    alias: dict[str, list[str]]
    style: list[Rule]

    @model_validator(mode="after")
    def check_alias(self):
        used = set()
        for style in self.style:
            used.update(style.fonts)
        has = set(self.alias)
        if used - has:
            raise ValueError(f"These alias is not found: {list(used - has)}")
        return self

    def to_css(self) -> str:
        styles = []
        key = '  font-family: '
        sep_with_indent = ",\n" + (' ' * len(key))
        for style in self.style:
            used = []
            for font in style.fonts:
                if self.alias[font]:
                    used.append(", ".join(s if ',' not in s else repr(s) for s in self.alias[font]))
            if not used:
                continue
            used_str = sep_with_indent.join(used)
            css = f"{style.filter} {{\n{key}{used_str};\n}}"
            styles.append(css)
        return "\n\n".join(styles)


def _load(name: str) -> str:
    with open(path_join(dirname(abspath(__file__)), name), mode="rt", encoding="utf8") as f:
        return f.read()


logger = getLogger(__name__)
_BANNED_MSG = "Some document try to load an unpermitted file (allow={allow}) via {url}"


def _remote_only_fetcher(url: str, timeout=10, ssl_context=None, http_headers=None):
    from weasyprint.urls import default_url_fetcher
    _ = timeout, ssl_context, http_headers
    if url.startswith("file"):
        logger.warning(_BANNED_MSG.format(allow="None", url=repr(url)))
        raise PermissionError("Path not allowed")
    return default_url_fetcher(url, timeout, ssl_context, http_headers)


def _get_safety_fetcher(base: str = None):
    from weasyprint.urls import default_url_fetcher
    if not base:
        return _remote_only_fetcher
    base = abspath(base)

    def safety_fetcher(url: str, timeout=10, ssl_context=None, http_headers=None):
        if url.startswith("file"):
            query_path = urlparse(url).path
            if commonpath([query_path, base]) != base:
                logger.warning(_BANNED_MSG.format(allow=repr(base), url=repr(url)))
                raise PermissionError("Path not allowed")
        return default_url_fetcher(url, timeout, ssl_context, http_headers)

    return safety_fetcher


def to_html(md_text: str, css_text: str = None,
            code_style: str = "monokai",
            code_css_class: str = "pygments-highlight") -> tuple[str, str]:
    """Returns a pair of (html, code_css)."""
    md_text = fix_md_list(md_text)
    body = _md2html_body(
        md_text,
        extensions=["extra", "fenced_code", "codehilite", "tables", "def_list", "nl2br"],
        extension_configs=dict(
            codehilite=dict(linenums=True, css_class=code_css_class)
        )
    )
    body = _fix_unexpected_break(body)
    pygments_css = HtmlFormatter(style=code_style).get_style_defs("." + code_css_class)
    css_text = f"<style>{css_text}</style>" if css_text else ""
    return HTML_TEMPLATE.format(style=css_text, body=body), pygments_css


def to_pdf(md_text: str, css_text: str | list[str] = None, resource_base_url: str = ".",
           allow_local_files: bool = False) -> bytes:
    from weasyprint import HTML, CSS

    if css_text and not isinstance(css_text, str):
        css_text = "\n\n".join(css_text)
    css_text: str
    html, code_css = to_html(md_text, css_text)

    if isdir(resource_base_url) and allow_local_files:
        fetcher = _get_safety_fetcher(resource_base_url)
    else:
        fetcher = _remote_only_fetcher

    builtin_css = [CSS(string=code_css), CSS(string=DEFAULT_FONT_CSS), CSS(string=DEFAULT_LAYOUT_CSS)]
    render = HTML(string=html, base_url=resource_base_url, url_fetcher=fetcher)
    pdf = render.write_pdf(stylesheets=builtin_css, optimize_images=True)
    return pdf


def load_fonts_to_css(font_yml: str) -> str:
    font_map = FontMap.model_validate(yaml.safe_load(font_yml))
    return font_map.to_css()


HTML_TEMPLATE = r"""<html>
<head><meta charset='utf-8'>{style}</head>
<body>{body}</body>
</html>
"""
DEFAULT_LAYOUT_CSS = _load("layout.css")
DEFAULT_FONT_CSS = load_fonts_to_css(_load("fonts.yaml"))
del _load


def fix_md_list(text: str) -> str:
    lines = text.splitlines(keepends=True)
    if not lines:
        return text
    in_code_block = False
    fence_char = None
    last_blank = True
    in_list = False
    output = []
    for raw in lines:
        strip = raw.rstrip()
        if strip.startswith("```") or strip.startswith("~~~"):
            # code block fence
            last_blank = False
            if not in_code_block:
                in_code_block = True
                fence_char = {strip[0]}
            elif set(strip) == fence_char:
                in_code_block = False
                fence_char = None
            output.append(raw)
            continue
        if in_code_block:
            last_blank = False
            output.append(raw)
            continue
        if not strip:
            last_blank = True
            in_list = False
            output.append(raw)
            continue
        if in_list:
            last_blank = False
            output.append(raw)
            continue

        ordered_list_prefix = strip.split(".", 1)[0].split(")", 1)[0]
        next_char_empty = not strip[min([len(ordered_list_prefix) + 1, len(strip) - 1])].strip()

        ordered_list = ordered_list_prefix.isdigit() and next_char_empty
        unordered_list = (strip[0] in "-*+") and (len(strip) > 1) and not strip[1].strip()
        if ordered_list or unordered_list:
            if not (in_list or last_blank):
                output.append("\n")
            in_list = True
            output.append(raw)
            continue
        last_blank = False
        output.append(raw)

    return "".join(output)


def _fix_unexpected_break(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.find_all(["p", "li"]):
        for br in tag.find_all("br"):
            if br.next_sibling and isinstance(br.next_sibling, str):
                br.next_sibling.replace_with(br.next_sibling.lstrip("\n"))
    return str(soup)
