"""
Repairs the malformed JSON that LLMs routinely emit around a valid payload.

Originates from the Alpha-Forge project by Anchit Lahkar
(https://github.com/Anchitlahkar/Alpha-Forge), reused under the MIT licence its
README declares. See CREDITS.md.
"""
import re


def repair_json(text: str) -> str:
    """Clean markdown artifacts and repair common malformed JSON from LLMs."""
    text = text.strip()

    # Remove markdown code blocks if any
    text = re.sub(r"```json\s*", "", text)
    text = re.sub(r"```\s*", "", text)
    text = text.strip()

    # Locate first '{' and last '}' to strip surrounding prose
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace != -1:
        text = text[first_brace:last_brace + 1]

    # Remove trailing commas before closing braces/brackets
    text = re.sub(r",\s*([\]}])", r"\1", text)

    # Escape raw newlines that appear inside string literals
    in_string = False
    escape = False
    chars = []
    for c in text:
        if c == '"' and not escape:
            in_string = not in_string
            chars.append(c)
        elif c == "\\" and in_string:
            escape = not escape
            chars.append(c)
        elif c == "\n" and in_string:
            chars.append("\n")
            escape = False
        else:
            escape = False
            chars.append(c)
    text = "".join(chars)

    # Balance braces and brackets if the response was truncated
    open_braces, close_braces = text.count("{"), text.count("}")
    open_brackets, close_brackets = text.count("["), text.count("]")
    if open_braces > close_braces:
        text += "}" * (open_braces - close_braces)
    if open_brackets > close_brackets:
        text += "]" * (open_brackets - close_brackets)

    return text
