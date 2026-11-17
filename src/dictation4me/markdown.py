"""Small safe Markdown preview. Never interprets raw HTML, remote resources or scripts."""
from __future__ import annotations

import html
import re


def _inline(text: str) -> str:
    # Escape first; allow only a controlled handful of inline formatting constructs.
    value = html.escape(text, quote=True)
    value = re.sub(r"\*\*([^*\n]{1,1000})\*\*", r"<strong>\1</strong>", value)
    value = re.sub(r"(?<!\*)\*([^*\n]{1,1000})\*(?!\*)", r"<em>\1</em>", value)
    value = re.sub(r"`([^`\n]{1,500})`", r"<code>\1</code>", value)
    return value


def render(text: str) -> str:
    """Render headings, bullets, code fences and paragraphs without any arbitrary HTML."""
    lines = text.splitlines()[:15000]
    output = []
    in_code = False
    in_list = False
    for line in lines:
        if line.strip().startswith("```"):
            if in_list:
                output.append("</ul>")
                in_list = False
            output.append("</code></pre>" if in_code else "<pre><code>")
            in_code = not in_code
            continue
        if in_code:
            output.append(html.escape(line, quote=True) + "\n")
            continue
        head = re.match(r"^(#{1,3})\s+(.+)", line)
        bullet = re.match(r"^\s*[-*+]\s+(.+)", line)
        if bullet:
            if not in_list:
                output.append("<ul>")
                in_list = True
            output.append("<li>" + _inline(bullet[1]) + "</li>")
            continue
        if in_list:
            output.append("</ul>")
            in_list = False
        if head:
            level = len(head[1])
            output.append(f"<h{level}>" + _inline(head[2]) + f"</h{level}>")
        elif line.strip():
            output.append("<p>" + _inline(line.strip()) + "</p>")
        else:
            output.append("<p></p>")
    if in_list:
        output.append("</ul>")
    if in_code:
        output.append("</code></pre>")
    return "".join(output)
