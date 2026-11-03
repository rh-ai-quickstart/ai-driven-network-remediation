"""Syntax-only YAML normalization that preserves ALS-generated behavior."""

import re

_UNQUOTED_JINJA_RE = re.compile(
    r"^(\s*(?:- )?[\w.\- ]+:[ \t]+)((?:.*\{\{.*?\}\}.*?))[ \t]*$",
    re.MULTILINE,
)


def quote_jinja(yaml_text: str) -> str:
    """Quote bare Jinja2 expressions so the YAML is valid for Ansible."""

    def _replacer(m):
        prefix, value = m.group(1), m.group(2)
        if value.startswith('"') or value.startswith("'"):
            return m.group(0)
        return f'{prefix}"{value}"'

    return _UNQUOTED_JINJA_RE.sub(_replacer, yaml_text)
