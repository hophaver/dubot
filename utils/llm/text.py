"""Cleanup and parsing of raw model output."""
import json
import re
from typing import Any, Dict, List

def extract_json_object(text: str) -> Dict[str, Any]:
    if not text:
        return {}
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```(?:json)?\s*", "", candidate, flags=re.IGNORECASE)
        candidate = re.sub(r"\s*```$", "", candidate)
    try:
        parsed = json.loads(candidate)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", candidate, flags=re.DOTALL)
    if not match:
        return {}
    try:
        parsed = json.loads(match.group(0))
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def extract_json_object_loose(text: str) -> Dict[str, Any]:
    """Parse first JSON object in model output; tolerates preamble/postamble."""
    t = clean_response(text or "").strip()
    if not t:
        return {}
    parsed = extract_json_object(t)
    if parsed:
        return parsed
    start = t.find("{")
    end = t.rfind("}")
    if start != -1 and end > start:
        try:
            chunk = t[start : end + 1]
            parsed = json.loads(chunk)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            pass
    return {}


def _json_object_end_index(s: str, start: int) -> int:
    """Index after the closing `}` of a JSON object starting at s[start] == '{', or -1 if incomplete."""
    depth = 0
    in_str = False
    esc = False
    for idx in range(start, len(s)):
        c = s[idx]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return idx + 1
    return -1


def strip_leaked_agent_tool_artifacts(text: str) -> str:
    """
    Remove ReAct-style tool call text or fenced JSON tool payloads that some chat
    models emit before prose (e.g. Action: dalle.text2im + Action Input: {...}).
    """
    if not (text or "").strip():
        return text or ""

    def _fenced_json_looks_like_tool(inner: str) -> bool:
        s = (inner or "").strip().lower()
        if not s.startswith("{"):
            return False
        if "text2im" in s or "dalle." in s or "image_generation" in s:
            return True
        if '"action"' in s or "'action'" in s:
            if "action_input" in s or "action input" in s:
                return True
        return False

    t = str(text)
    # Remove from the end so earlier match positions stay valid.
    for m in reversed(list(re.finditer(r"```\s*json\s*[\r\n]+([\s\S]*?)```", t, flags=re.IGNORECASE))):
        if _fenced_json_looks_like_tool(m.group(1)):
            t = t[: m.start()] + t[m.end() :]

    lines = t.splitlines()
    out_lines: List[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if (
            re.match(r"^\s*Thought:\s*", line, flags=re.IGNORECASE)
            and i + 1 < len(lines)
            and re.match(r"^\s*Action:\s*\S", lines[i + 1], flags=re.IGNORECASE)
        ):
            i += 1
            continue
        m_action = re.match(r"^\s*Action:\s*\S", line, flags=re.IGNORECASE)
        if m_action:
            i += 1
            if i < len(lines) and re.match(r"^\s*Action Input:\s*", lines[i], flags=re.IGNORECASE):
                buf = re.sub(r"^\s*Action Input:\s*", "", lines[i], flags=re.IGNORECASE).lstrip()
                i += 1
                jb = buf.find("{")
                if jb != -1:
                    end = _json_object_end_index(buf, jb)
                    while end == -1 and i < len(lines):
                        buf = buf + "\n" + lines[i]
                        i += 1
                        end = _json_object_end_index(buf, jb)
                    if end != -1:
                        tail = buf[end:].strip()
                        if tail:
                            out_lines.append(tail)
                continue
            continue
        out_lines.append(line)
        i += 1

    t = "\n".join(out_lines)
    t = re.sub(r"(?im)^\s*Observation:\s*.*(?:\n|$)", "", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def strip_leaked_image_placeholders(text: str) -> str:
    """Remove fake image-attachment text some models emit; never show to users."""
    if not (text or "").strip():
        return text or ""
    t = str(text)
    t = re.sub(r"\[Sent a generated image:\s*[^\]]*\]", "", t, flags=re.IGNORECASE | re.DOTALL)
    t = strip_leaked_agent_tool_artifacts(t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def clean_response(text):
    """Clean up LLM response"""
    if not text:
        return ""
    
    # Remove unnecessary prefixes
    text = re.sub(r'^assistant:\s*', '', text, flags=re.IGNORECASE)
    text = re.sub(r'^ai:\s*', '', text, flags=re.IGNORECASE)
    text = re.sub(r'^bot:\s*', '', text, flags=re.IGNORECASE)
    # Don't parrot "X says:" or "X is asking" at start of reply
    text = re.sub(r'^[\w.]+\s+(?:says|is asking):\s*', '', text, flags=re.IGNORECASE)
    
    # Clean up extra whitespace
    text = re.sub(r'\n\s*\n\s*\n', '\n\n', text)
    
    return text.strip()
