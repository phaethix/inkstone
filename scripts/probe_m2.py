"""M2 probe — verify the Agnes server-side contracts before writing M2 code.

Covers M2-design.md §9:
  - R1: i2i via ``extra_body.image=[url]`` + ``response_format="url"`` still 200.
  - R3: multi-image reference (<=9) does not 400.
  - R4: forced function calling (tool_choice) returns valid JSON for extraction.

Reuses M1's ``get_image_provider`` for image calls; the chat call is raw
``requests`` because ``ChatProvider`` is an M2 deliverable, not yet written.

Run from the repo root:  python scripts/probe_m2.py
Artifacts (image URLs, raw JSON) are printed; no files are committed.
"""

import asyncio
import json
import os

import requests


# Minimal .env loader (no python-dotenv dependency).
def _load_dotenv(path: str = ".env") -> None:
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip().strip('"').strip("'")
            os.environ.setdefault(key, val)


_load_dotenv()

CHAT_URL = "https://apihub.agnes-ai.com/v1/chat/completions"
CHAT_MODEL = "agnes-2.0-flash"
CHAT_HEADERS = {
    "Authorization": f"Bearer {os.environ.get('AGNES_API_KEY', '')}",
    "Content-Type": "application/json",
}


def _print(label: str, ok: bool, detail: str = "") -> None:
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {label}" + (f" — {detail}" if detail else ""))


# Probes A / C: image generation (t2i, i2i, multi-ref) via M1 provider.
async def probe_images(provider) -> dict:
    results: dict = {}

    # t2i: generate a base portrait to use as reference (R1 setup).
    print("\nImage probes (reuse M1 provider):")
    try:
        out = await provider.generate_single_image(
            "a bespectacled cat, ink-wash manhua style, character design sheet",
            size="1024x1024",
        )
        base_url = out.data if out.fmt == "url" else "<b64>"
        results["base_url"] = base_url
        _print("t2i generation", bool(base_url), base_url[:80])
    except Exception as e:  # noqa: BLE001
        _print("t2i generation", False, repr(e))
        return results

    # R1: i2i with a single reference url (extra_body.image=[url]).
    try:
        out2 = await provider.generate_single_image(
            "the same bespectacled cat, reading a book, ink-wash manhua style",
            reference_image_paths=[results["base_url"]],
            size="1024x1024",
        )
        i2i_url = out2.data if out2.fmt == "url" else "<b64>"
        results["i2i_url"] = i2i_url
        _print("R1 i2i (single ref url)", bool(i2i_url), i2i_url[:80])
    except Exception as e:  # noqa: BLE001
        _print("R1 i2i (single ref url)", False, repr(e))

    # R3: multi-image reference (two urls) must not 400.
    try:
        out3 = await provider.generate_single_image(
            "the bespectacled cat standing on a ship deck at dawn, ink-wash manhua style",
            reference_image_paths=[
                results["base_url"],
                results.get("i2i_url", results["base_url"]),
            ],
            size="1024x1024",
        )
        multi_url = out3.data if out3.fmt == "url" else "<b64>"
        results["multi_url"] = multi_url
        _print("R3 multi-image ref (2 urls)", bool(multi_url), multi_url[:80])
    except Exception as e:  # noqa: BLE001
        _print("R3 multi-image ref (2 urls)", False, repr(e))

    return results


# Probe B: forced function calling for story extraction (R4).
def probe_chat_function_calling() -> bool:
    print("\nChat probe (R4: forced function calling):")
    if not os.environ.get("AGNES_API_KEY"):
        _print("R4 env AGNES_API_KEY", False, "key missing")
        return False

    tool = {
        "type": "function",
        "function": {
            "name": "extract_story_elements",
            "description": "Extract character and setting assets from a novel excerpt.",
            "parameters": {
                "type": "object",
                "properties": {
                    "characters": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "role": {"type": "string"},
                                "appearance": {"type": "object"},
                            },
                            "required": ["name"],
                        },
                    },
                    "settings": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "description": {"type": "string"},
                            },
                            "required": ["name"],
                        },
                    },
                },
                "required": ["characters", "settings"],
            },
        },
    }

    excerpt = (
        "方鸿渐是个清瘦的年轻人，戴着圆框眼镜，穿着白色立领衬衫和深灰马甲。"
        "清晨的远洋邮轮甲板上，海风拂过，他扶着船舷眺望海面。"
    )
    payload = {
        "model": CHAT_MODEL,
        "messages": [
            {
                "role": "system",
                "content": "You extract structured story elements. Respond only via the tool.",
            },
            {"role": "user", "content": excerpt},
        ],
        "tools": [tool],
        "tool_choice": {"type": "function", "function": {"name": "extract_story_elements"}},
    }

    try:
        resp = requests.post(CHAT_URL, headers=CHAT_HEADERS, json=payload, timeout=(30, 120))
        _print("R4 chat http status", resp.status_code == 200, f"status={resp.status_code}")
        if resp.status_code != 200:
            _print("R4 chat body", False, resp.text[:300])
            return False
        body = resp.json()
        msg = body["choices"][0]["message"]
        calls = msg.get("tool_calls") or []
        if not calls:
            _print("R4 tool_calls present", False, f"message={json.dumps(msg)[:300]}")
            return False
        args_raw = calls[0]["function"]["arguments"]
        parsed = json.loads(args_raw)  # must be valid JSON
        chars = parsed.get("characters", [])
        settings = parsed.get("settings", [])
        ok = bool(chars) and bool(settings)
        _print(
            "R4 valid JSON + structure",
            ok,
            f"chars={[c.get('name') for c in chars]}, settings={[s.get('name') for s in settings]}",
        )
        print("    raw arguments:", args_raw[:400])
        return ok
    except Exception as e:  # noqa: BLE001
        _print("R4 chat call", False, repr(e))
        return False


async def main() -> None:
    from core.api import get_image_provider

    print("M2 probe starting — verifying Agnes server contracts (R1/R3/R4)")
    provider = get_image_provider()
    img_results = await probe_images(provider)
    chat_ok = probe_chat_function_calling()

    print("\nSummary:")
    has_i2i = bool(img_results.get("i2i_url"))
    has_multi = bool(img_results.get("multi_url"))
    print(f"  R1 i2i single-ref : {'ok' if has_i2i else 'FAILED'}")
    print(f"  R3 multi-ref      : {'ok' if has_multi else 'FAILED'}")
    print(f"  R4 function call  : {'ok' if chat_ok else 'FAILED'}")
    all_ok = has_i2i and has_multi and chat_ok
    print(f"  OVERALL           : {'ALL PASS' if all_ok else 'SEE FAILURES ABOVE'}")


if __name__ == "__main__":
    asyncio.run(main())
