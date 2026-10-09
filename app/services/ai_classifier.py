import base64, json, os, re
from typing import Literal, Optional, get_args
import httpx
from pydantic import BaseModel, Field, ValidationError, field_validator

Material = Literal["Concrete", "Metal", "Brick", "Wood", "Glass", "Plastic", "Other", "Mixed Construction Waste"]
MATERIALS = get_args(Material)
MESSAGES = {
    "no_key": "AI classification isn't set up yet. Add ANTHROPIC_API_KEY to your .env file and restart the server, or select the material manually.",
    "auth": "The AI service rejected the API key. Check ANTHROPIC_API_KEY, or select the material manually.",
    "timeout": "The AI service timed out. Try again, or select the material manually.",
    "api": "The AI service returned an error ({code}). Try again, or select the material manually.",
    "bad": "The AI response could not be understood. Please select the material manually.",
}
PROMPT = ('Identify the main construction waste material in this photo. Reply with ONLY JSON: {"material": one of '
          'Concrete|Metal|Brick|Wood|Glass|Plastic|Other|Mixed Construction Waste, "confidence": a number 0-1 only if you can '
          'justify it, otherwise null, "recyclable": true|false, "recommended_action": short string, "possible_reuse": short string}.')


class Classification(BaseModel):
    material: Material
    confidence: Optional[float] = Field(default=None, ge=0, le=1)
    recyclable: bool
    recommended_action: str
    possible_reuse: str = ""

    @field_validator("material", mode="before")
    @classmethod
    def to_supported(cls, v):  # map the model's wording onto our categories; unknown -> Other
        return {m.lower(): m for m in MATERIALS}.get(str(v).strip().lower(), "Other")


def parse(text: str) -> Classification:
    return Classification.model_validate(json.loads(re.search(r"\{.*\}", text, re.S).group(0)))


def classify(data: bytes, media_type: str):
    """Returns (Classification, None) or (None, safe user-facing message). Never raises, never logs the key."""
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        return None, MESSAGES["no_key"]
    try:
        r = httpx.post("https://api.anthropic.com/v1/messages", timeout=30,
                       headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                       json={"model": os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5"), "max_tokens": 400,
                             "messages": [{"role": "user", "content": [
                                 {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": base64.b64encode(data).decode()}},
                                 {"type": "text", "text": PROMPT}]}]})
        r.raise_for_status()
        return parse(r.json()["content"][0]["text"]), None
    except httpx.TimeoutException:
        return None, MESSAGES["timeout"]
    except httpx.HTTPStatusError as e:
        code = e.response.status_code
        print(f"Anthropic API error {code}: {e.response.text[:1000]}")
        return None, MESSAGES["auth"] if code in (401, 403) else MESSAGES["api"].format(code=code)
    except httpx.HTTPError:
        return None, MESSAGES["api"].format(code="network")
    except (ValueError, KeyError, IndexError, AttributeError, TypeError, ValidationError):
        return None, MESSAGES["bad"]
