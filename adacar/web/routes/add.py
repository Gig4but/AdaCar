"""Ads the user adds by hand (PLAN §6 manual add), and the pictures they added."""

import io
import re
import uuid
from typing import Annotated

from fastapi import APIRouter, Request
from fastapi import Path as InPath
from fastapi.responses import FileResponse, HTMLResponse, Response
from sqlalchemy import select
from starlette.datastructures import UploadFile

from adacar.ai.services import UPLOADS
from adacar.core import ParsedCar, utcnow
from adacar.db.models import Car, CarAd, CarImage
from adacar.i18n import translate
from adacar.normalize import cars
from adacar.scraper.sources import MANUAL
from adacar.scraper.sources.bazos import add_facts
from adacar.scraper.store import CarStore
from adacar.search.service import start_batch
from adacar.web.pages import Svc, render

router = APIRouter()

MAX_UPLOAD = 15_000_000
PICTURE_SIDE = 1600  # enough for the AI to read a screenshot; phones send 4000 px photos
UPLOAD_NAME = r"[0-9a-f]{32}\.jpg"


def keep_picture(folder, data: bytes) -> str:
    """Saves an added picture as a JPEG; its URL. ValueError: not a picture."""
    from PIL import Image, ImageOps

    try:
        with Image.open(io.BytesIO(data)) as img:
            img = ImageOps.exif_transpose(img).convert("RGB")  # a phone photo upright
            img.thumbnail((PICTURE_SIDE, PICTURE_SIDE))
            out = io.BytesIO()
            img.save(out, format="JPEG", quality=88)
    except (OSError, Image.DecompressionBombError) as exc:  # not a picture, or a huge one
        raise ValueError("not a picture") from exc
    name = f"{uuid.uuid4().hex}.jpg"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(out.getvalue())
    return f"/{UPLOADS}/{name}"


@router.get("/add", response_class=HTMLResponse)
def add_page(request: Request) -> HTMLResponse:
    return render(request, "add.html")


@router.post("/add", response_class=HTMLResponse)
async def add_ad(request: Request, svc: Svc) -> HTMLResponse:
    """An ad the user pasted, or added as a screenshot."""
    form = await request.form()
    text = str(form.get("text") or "").strip()[:20000]
    link = str(form.get("link") or "").strip()
    upload = form.get("picture")
    upload = upload if isinstance(upload, UploadFile) and upload.filename else None
    data = await upload.read(MAX_UPLOAD + 1) if upload else b""
    again = {"text": text, "link": link}
    if not text and not data:
        return render(request, "add.html", error="add.empty", **again)
    if len(data) > MAX_UPLOAD:
        return render(request, "add.html", error="add.too_big", **again)
    picture = None
    if data:
        try:
            picture = keep_picture(svc.data_dir / UPLOADS, data)
        except ValueError:
            return render(request, "add.html", error="add.not_picture", **again)
    first = text.split("\n", 1)[0].strip()[:90] if text else ""
    parsed = ParsedCar(
        external_id=uuid.uuid4().hex,
        title=first or translate(svc.settings.language, "add.picture_title"),
        url=link if re.match(r"^https?://", link) else None,
        description=text,
        posted_at=utcnow(),
    )
    if text:
        add_facts(parsed, None)
        parsed.price = cars.price_in_text(text)
    else:
        parsed.extra["title_from_picture"] = True
    with svc.sessions() as s:
        if not svc.runner.progress.running:
            start_batch(s)  # listed on top, like a search's finds
        store = CarStore(s, MANUAL)
        store.add(parsed)
        store.commit()
        car = s.scalars(
            select(Car)
            .join(CarAd)
            .where(CarAd.source_id == MANUAL, CarAd.external_id == parsed.external_id)
        ).one()
        if picture is not None:
            car.images.append(
                CarImage(url=picture, position=len(car.images), kind="upload", created_at=utcnow())
            )
        s.commit()
        _ = car.ads, car.images, car.location  # loaded for the card
    svc.ai.enricher.start()  # reads the picture and fills the fields in the background
    return render(request, "add.html", car=car)


@router.get(f"/{UPLOADS}/{{name}}")
def added_picture(svc: Svc, name: Annotated[str, InPath(pattern=UPLOAD_NAME)]) -> Response:
    path = svc.data_dir / UPLOADS / name
    if not path.exists():
        return Response(status_code=404)
    return FileResponse(path, media_type="image/jpeg")
