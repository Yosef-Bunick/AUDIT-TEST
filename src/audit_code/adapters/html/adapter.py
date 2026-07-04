"""HTML/CSS adapter — self-contained structural well-formedness checks.

HTML: stdlib HTMLParser drives a tag-balance check (stray closing tags,
unclosed elements) with void-element awareness. CSS/SCSS: brace balance,
string/comment aware. These catch genuinely broken files; they are NOT a
full spec validator, and the result says so. Findings are MEDIUM (browsers
tolerate malformed markup), so the audit warns rather than hard-fails.
"""

from html.parser import HTMLParser
from pathlib import Path

from audit_code.adapters.base import LanguageAdapter, rel
from audit_code.models import Severity

_VOID = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}
# Elements the HTML spec auto-closes; unclosed is legal, not a defect.
_OPTIONAL_CLOSE = {
    "p",
    "li",
    "td",
    "tr",
    "th",
    "dt",
    "dd",
    "option",
    "html",
    "body",
    "head",
}


class _TagBalancer(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list = []  # (tag, line)
        self.problems: list = []  # (line, message)

    def handle_starttag(self, tag, attrs):
        if tag not in _VOID:
            self.stack.append((tag, self.getpos()[0]))

    def handle_startendtag(self, tag, attrs):
        pass  # self-closing — nothing to balance

    def handle_endtag(self, tag):
        line = self.getpos()[0]
        if tag in _VOID:
            return
        open_tags = [t for t, _ in self.stack]
        if tag not in open_tags:
            self.problems.append((line, f"stray closing tag </{tag}>"))
            return
        while self.stack:
            open_tag, open_line = self.stack.pop()
            if open_tag == tag:
                break
            if open_tag not in _OPTIONAL_CLOSE:
                self.problems.append(
                    (
                        open_line,
                        f"<{open_tag}> never closed (implicitly "
                        f"closed by </{tag}> at line {line})",
                    )
                )

    def finish(self):
        for open_tag, open_line in self.stack:
            if open_tag not in _OPTIONAL_CLOSE:
                self.problems.append((open_line, f"<{open_tag}> never closed"))


def _check_css_braces(text: str) -> list:
    """Return (line, message) for unbalanced braces, skipping strings/comments."""
    problems = []
    depth = 0
    line = 1
    i = 0
    n = len(text)
    in_str: str | None = None
    while i < n:
        ch = text[i]
        if ch == "\n":
            line += 1
            in_str = None  # CSS strings do not span raw newlines
        elif in_str:
            if ch == "\\":
                i += 1
            elif ch == in_str:
                in_str = None
        elif ch in "\"'":
            in_str = ch
        elif ch == "/" and i + 1 < n and text[i + 1] == "*":
            end = text.find("*/", i + 2)
            if end == -1:
                break
            line += text.count("\n", i, end)
            i = end + 1
        elif ch == "/" and i + 1 < n and text[i + 1] == "/":
            nl = text.find("\n", i)
            i = (nl - 1) if nl != -1 else n
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth < 0:
                problems.append((line, "unmatched closing brace '}'"))
                depth = 0
        i += 1
    if depth > 0:
        problems.append((line, f"{depth} unclosed brace(s) '{{' at end of file"))
    return problems


class HtmlAdapter(LanguageAdapter):
    """Language adapter for HTML/CSS projects."""

    language = "html"
    extensions = (".html", ".htm", ".css", ".scss")
    marker_files = ()

    @classmethod
    def check_files(cls, root: Path, files: list):
        findings = []
        html_n = css_n = 0
        for f in files:
            text = f.read_text(encoding="utf-8", errors="replace")
            if f.suffix in (".html", ".htm"):
                html_n += 1
                parser = _TagBalancer()
                try:
                    parser.feed(text)
                    parser.close()
                except Exception as e:  # HTMLParser rarely raises; fail closed
                    parser.problems.append((0, f"parser error: {e}"))
                parser.finish()
                for line, msg in parser.problems[:20]:
                    findings.append(
                        cls.finding(
                            msg,
                            file=rel(f, root),
                            line=line or None,
                            severity=Severity.MEDIUM,
                        )
                    )
            else:
                css_n += 1
                for line, msg in _check_css_braces(text)[:20]:
                    findings.append(
                        cls.finding(
                            msg,
                            file=rel(f, root),
                            line=line,
                            severity=Severity.MEDIUM,
                        )
                    )
        notes = [
            f"{html_n} HTML file(s) tag-balance checked, "
            f"{css_n} CSS/SCSS file(s) brace-balance checked "
            "(structural check — not a full spec validator)"
        ]
        return cls.result(findings, notes)
