import asyncio
import json
from uuid import UUID

import httpx
from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, PositiveInt, field_validator
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.db import CampaignCreation
from app.config import ROOT


router = APIRouter(prefix="/api", tags=["Campaign creation"])


class CampaignInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    name: str = Field(min_length=1, max_length=255)
    country_code: str = Field(pattern=r"^[A-Z]{2}$")
    offer_id: PositiveInt
    domain_id: PositiveInt | None = None
    group_id: PositiveInt | None = None
    traffic_source_id: PositiveInt | None = None

    @field_validator("country_code", mode="before")
    @classmethod
    def normalize_country(cls, value):
        return value.strip().upper() if isinstance(value, str) else value


class Choice(BaseModel):
    id: PositiveInt
    name: str


class Country(BaseModel):
    code: str
    name: str


COUNTRIES = [Country(**row) for row in json.loads((ROOT / "app/countries.json").read_text(encoding="utf-8"))]


class Choices(BaseModel):
    mode: str
    countries: list[Country]
    domains: list[Choice]
    groups: list[Choice]
    traffic_sources: list[Choice]
    offers: list[Choice]
    defaults: dict[str, int | None]


class Creation(BaseModel):
    request_id: UUID
    status: str
    keitaro_campaign_id: int | None
    geo_stream_id: int | None
    offer_stream_id: int | None


async def read_list(client, path):
    try:
        response = await client.get(path)
        response.raise_for_status()
        rows = response.json()
        if not isinstance(rows, list):
            raise ValueError()
        return rows
    except (ValueError, httpx.HTTPError) as exc:
        raise HTTPException(502, f"Не удалось прочитать {path} из Keitaro") from exc


@router.get("/campaign-options", response_model=Choices)
async def options(request: Request):
    paths = ["domains", "groups?type=campaigns", "traffic_sources", "offers"]
    results = await asyncio.gather(*(read_list(request.app.state.keitaro, p) for p in paths))
    try:
        return Choices(mode=request.app.state.settings.adrobot_mode, countries=COUNTRIES, defaults={
            field: 1 if request.app.state.settings.adrobot_mode == "demo" else getattr(request.app.state.settings, "default_" + field)
            for field in ("domain_id", "group_id", "traffic_source_id")
        }, **{
            key: [Choice(id=row["id"], name=row.get("name") or row.get("domain"))
                  for row in rows if row.get("state") not in ("disabled", "deleted")]
            for key, rows in zip(("domains", "groups", "traffic_sources", "offers"), results)
        })
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise HTTPException(502, "Неожиданный формат справочника Keitaro") from exc


def find_operation(session, key):
    return session.scalar(select(CampaignCreation).where(CampaignCreation.request_id == str(key)))


@router.get("/campaign-creations/{key}", response_model=Creation)
def get_creation(key: UUID, request: Request):
    with request.app.state.sessions() as session:
        row = find_operation(session, key)
        if row is None:
            raise HTTPException(404, "Запрос создания не найден")
        return Creation.model_validate(row, from_attributes=True)


def transition(sessions, key, before, after, **values):
    with sessions.begin() as session:
        changed = session.execute(update(CampaignCreation).where(
            CampaignCreation.request_id == str(key), CampaignCreation.status == before
        ).values(status=after, **values))
        return changed.rowcount == 1


def stage_payload(stage, data, campaign_id, alias):
    if stage == "campaign":
        return "campaigns", {
            "name": data.name, "alias": alias, "domain_id": data.domain_id,
            "group_id": str(data.group_id), "traffic_source_id": data.traffic_source_id,
            "type": "position",
        }
    stream = {"campaign_id": campaign_id, "state": "active", "collect_clicks": True}
    if stage == "geo":
        return "streams", dict(stream, name="Google · " + data.country_code,
            type="regular", position=1, schema="redirect", action_type="http",
            action_payload="https://www.google.com/",
            filters=[{"name": "country", "mode": "accept", "payload": [data.country_code]}])
    return "streams", dict(stream, name="Offer", type="default", position=2, schema="landings",
        action_type="http",
        offers=[{"offer_id": data.offer_id, "share": 100, "state": "active"}])


@router.post("/campaigns", response_model=Creation)
async def create_campaign(data: CampaignInput, request: Request,
                          idempotency_key: UUID = Header(alias="Idempotency-Key")):
    key = idempotency_key
    sessions = request.app.state.sessions
    client = request.app.state.keitaro
    with sessions() as session:
        row = find_operation(session, key)
        for field in ("domain_id", "group_id", "traffic_source_id"):
            if getattr(data, field) is None:
                value = getattr(row, field) if row else getattr(request.app.state.settings, "default_" + field)
                if row is None and request.app.state.settings.adrobot_mode == "demo":
                    value = 1
                if value is None:
                    raise HTTPException(422, f"Укажите {field} или настройте значение по умолчанию")
                setattr(data, field, value)
        if row is None:
            if data.country_code not in {country.code for country in COUNTRIES}:
                raise HTTPException(422, "Unknown country code")
            if request.app.state.settings.adrobot_mode == "demo":
                catalog = await options(request)
                for field, entries in (("domain_id", catalog.domains), ("group_id", catalog.groups),
                                       ("traffic_source_id", catalog.traffic_sources), ("offer_id", catalog.offers)):
                    if getattr(data, field) not in {entry.id for entry in entries}:
                        raise HTTPException(422, f"Unknown {field}")
            session.add(CampaignCreation(request_id=str(key), status="campaign", **data.model_dump()))
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
            row = find_operation(session, key)
        if row is None:
            raise HTTPException(500, "Не удалось сохранить запрос")
        if any(getattr(row, k) != v for k, v in data.model_dump().items()):
            raise HTTPException(409, "Этот Idempotency-Key уже связан с другими параметрами")

    alias = "ad" + key.hex
    for stage, following, id_column in [
        ("campaign", "geo", "keitaro_campaign_id"),
        ("geo", "offer", "geo_stream_id"),
        ("offer", "completed", "offer_stream_id"),
    ]:
        current = get_creation(key, request)
        sending = "sending_" + stage
        path, payload = stage_payload(stage, data, current.keitaro_campaign_id, alias)
        if current.status == sending:
            # A previous POST may have succeeded even when its response was lost.
            if stage == "campaign":
                rows = await read_list(client, "campaigns")
                matches = [r for r in rows if r.get("alias") == alias]
            else:
                rows = await read_list(client, f"campaigns/{current.keitaro_campaign_id}/streams")
                matches = [r for r in rows if r.get("name") == payload["name"]
                           and r.get("type") == payload["type"]]
            if len(matches) != 1:
                raise HTTPException(409, "Результат записи пока не подтверждён. Проверьте состояние в Keitaro и повторите с тем же ключом.")
            remote_id = int(matches[0]["id"])
        elif current.status == stage:
            if not transition(sessions, key, stage, sending):
                raise HTTPException(409, "Запрос уже обрабатывается")
            try:
                response = await client.post(path, json=payload)
                if 400 <= response.status_code < 500 and response.status_code not in (408, 409):
                    transition(sessions, key, sending, stage)
                response.raise_for_status()
                remote_id = int(response.json()["id"])
            except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
                if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)):
                    transition(sessions, key, sending, stage)
                status = f" HTTP {exc.response.status_code}." if isinstance(exc, httpx.HTTPStatusError) else ""
                if isinstance(exc, httpx.HTTPStatusError):
                    try:
                        body = exc.response.json()
                    except ValueError:
                        body = None
                    if isinstance(body, dict):
                        reason = body.get("error") or body.get("message") or body.get("errors")
                        if reason:
                            key_value = request.app.state.settings.keitaro_api_key.get_secret_value()
                            reason = str(reason).replace(key_value, "***") if key_value else str(reason)
                            status += " " + reason[:500]
                raise HTTPException(502, f"Ошибка Keitaro на этапе {stage}.{status} Проверьте состояние запроса.") from exc
        else:
            continue
        transition(sessions, key, sending, following, **{id_column: remote_id})
    return get_creation(key, request)
