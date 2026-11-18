"""Pictures, read by the model with its vision part (`mmproj`, loaded only for this work):

- the cover photo (PLAN §6): what a car's photo shows, so the list can open with the car
  from the front rather than a dealer's logo, a banner or the interior;
- an added picture (a screenshot of an ad, PLAN §6 manual add): its text is written out and
  then goes through the same extraction and checks as any other ad.
"""

import io
import re

from adacar.ai.client import LlmClient, Reply, image_part

PROMPT_VERSION = 2
MAX_IMAGE_SIDE = 1024  # larger images cost much more time and add little for screenshots
PHOTO_SIDE = 448  # what a photo shows is clear at this size, and it costs few tokens
MAX_REPEATS = 2  # a line the model writes more often in a row is a loop, not the picture

SYSTEM = """Přepiš veškerý čitelný text z obrázku přesně tak, jak je napsaný, řádek po řádku.
Nic nepřidávej, nevysvětluj a nepřekládej. Pokud na obrázku žádný text není, napiš jen: -"""

# The order matters: with the front view first, models took everything for a front view (a
# side view, a rear view, a banner); in this order Qwen3-VL 4B got all six test photos right.
PHOTO_SYSTEM = """Co je na fotografii z inzerátu na prodej auta? Vyber jednu možnost:
- "jiné": logo, nápis, banner, doklady nebo něco jiného než auto;
- "interiér": sedadla, palubní deska, kufr zevnitř;
- "detail": jen část auta (kolo, motor, světlo, tachometr, poškození);
- "bok nebo zadek": celé auto zvenku z boku nebo zezadu, předek není vidět;
- "předek": celé auto zvenku, je vidět jeho předek (maska a přední světla), i šikmo."""
PHOTO_KINDS = {
    "jiné": "other",
    "interiér": "interior",
    "detail": "detail",
    "bok nebo zadek": "exterior",
    "předek": "front",
}
PHOTO_SCHEMA = {
    "type": "object",
    "properties": {"foto": {"enum": list(PHOTO_KINDS)}},
    "required": ["foto"],
    "additionalProperties": False,
}


def shrink(data: bytes, max_side: int = MAX_IMAGE_SIDE) -> tuple[bytes, str]:
    """JPEG bytes no larger than `max_side` px (phones send 4000 px photos)."""
    from PIL import Image

    with Image.open(io.BytesIO(data)) as img:
        img = img.convert("RGB")
        img.thumbnail((max_side, max_side))
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=88)
    return out.getvalue(), "image/jpeg"


def classify(client: LlmClient, data: bytes) -> Reply:
    """What a car photo shows: front | exterior | interior | detail | other."""
    jpeg, mime = shrink(data, PHOTO_SIDE)
    reply = client.chat(
        [
            {"role": "system", "content": PHOTO_SYSTEM},
            {"role": "user", "content": [image_part(jpeg, mime)]},
        ],
        schema=PHOTO_SCHEMA,
        max_tokens=20,
    )
    return Reply(PHOTO_KINDS.get(str(reply.data.get("foto")), "other"), reply.timing)


def transcribe(client: LlmClient, data: bytes) -> Reply:
    """The image's text (empty when there is none). Needs a vision-capable launch."""
    jpeg, mime = shrink(data)
    reply = client.chat(
        [
            {"role": "system", "content": SYSTEM},
            {
                "role": "user",
                "content": [image_part(jpeg, mime), {"type": "text", "text": "Přepis:"}],
            },
        ],
        max_tokens=800,  # a full screenshot of an ad; a longer text is kept as far as it got
    )
    return Reply(clean(str(reply.data)), reply.timing)


def clean(text: str) -> str:
    """Without an echoed "Přepis:" and without loops (a real ad picture got "500" written
    400 times); "-" or nothing means the picture has no text."""
    text = re.sub(r"^\s*Přepis:\s*", "", text.strip())
    lines: list[str] = []
    run = 0
    for line in text.split("\n"):
        same = bool(lines) and bool(line.strip()) and line.strip() == lines[-1].strip()
        run = run + 1 if same else 0
        if run < MAX_REPEATS:
            lines.append(line)
    text = "\n".join(lines).strip()
    return "" if text in ("-", "") else text
