"""Project user-visible Codex messages without tool output, memory or reasoning."""
import json
import re


def visible_text(value, *, structured_reply=False):
    if not isinstance(value, str) or not value.strip():
        return ""
    if structured_reply and value.lstrip().startswith("{"):
        try:
            obj = json.loads(value)
        except ValueError:
            if re.match(r'^\s*\{\s*"(?:reply|memory)', value):
                return ""
            obj = None
        if isinstance(obj, dict) and ("reply" in obj or "memory" in obj):
            value = obj.get("reply", "")
    if not isinstance(value, str):
        return ""
    value = re.sub(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)", "[credential removed]", value)
    value = re.sub(r"\b(?:gh[pousr]_|github_pat_|sk-|hvs\.|xox[baprs]-)[\w.-]{10,}", "[credential removed]", value)
    value = re.sub(r"\b(?:Bearer|Basic)\s+[A-Za-z0-9_./+=-]+", "[authorization removed]", value, flags=re.I)
    return re.sub(r"\b[A-Z0-9_]*(?:TOKEN|PASSWORD|SECRET|API_KEY|DATABASE_URL|PRIVATE_KEY)\s*[:=]\s*(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)", "[configuration value removed]", value, flags=re.I)
