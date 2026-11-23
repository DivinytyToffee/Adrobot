from hashlib import sha256
import json

from fastapi import HTTPException
from pydantic import BaseModel, Field, PositiveInt


class Offer(BaseModel):
    offer_id: PositiveInt
    state: str
    share: int


class EditResult(BaseModel):
    offers: list[Offer]
    revision: str


class Stream(EditResult):
    id: PositiveInt
    name: str
    position: int | None
    editable: bool
    pinned: dict[int, int]


class Edit(BaseModel):
    revision: str
    active_ids: list[PositiveInt]
    pinned: dict[int, int] = Field(default_factory=dict)


def version(rows, pinned):
    return sha256(json.dumps([rows, {str(k): v for k, v in pinned.items()}], sort_keys=True).encode()).hexdigest()


def distribute(ids, pinned):
    if not ids or len(ids) != len(set(ids)):
        raise HTTPException(422, "Нужен хотя бы один активный оффер. ID должны быть уникальными.")
    if not pinned.keys() <= set(ids) or any(v < 0 or v > 100 for v in pinned.values()):
        raise HTTPException(422, "Проверьте закреплённые доли.")
    free = [i for i in ids if i not in pinned]
    remainder = 100 - sum(pinned.values())
    if remainder < 0 or (not free and remainder):
        raise HTTPException(422, "Сумма долей должна составлять 100%.")
    shares = dict(pinned)
    if free:
        base, extra = divmod(remainder, len(free))
        shares.update({i: base + int(n < extra) for n, i in enumerate(free)})
    return shares


def preview(rows, edit, catalog, pinned):
    if version(rows, pinned) != edit.revision:
        raise HTTPException(409, "Поток изменился. Обновите данные.")
    old = {int(r["offer_id"]): r for r in rows}
    if set(edit.active_ids) - (set(catalog) | old.keys()):
        raise HTTPException(422, "Неизвестный ID оффера.")
    shares = distribute(edit.active_ids, edit.pinned)
    return [dict(offer_id=i, share=shares.get(i,0), state="active" if i in shares else "disabled")
            for i in dict.fromkeys([*old, *edit.active_ids])]
