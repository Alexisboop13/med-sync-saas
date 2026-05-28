from __future__ import annotations

import uuid
from typing import List

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import select

from app.api.deps import AnyStaff, OwnerOnly, TenantContext
from app.core.limiter import limiter
from app.models.location import Location
from app.schemas.location import LocationCreate, LocationResponse, LocationUpdate

router = APIRouter(prefix="/locations", tags=["Locations"])


@router.get("", response_model=List[LocationResponse])
@limiter.limit("100/minute")
async def list_locations(
    request: Request,
    ctx: TenantContext,
    _: AnyStaff,
):
    result = await ctx.db.execute(
        select(Location).where(Location.clinic_id == ctx.clinic_id)
    )
    return result.scalars().all()


@router.post("", response_model=LocationResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("100/minute")
async def create_location(
    request: Request,
    body: LocationCreate,
    ctx: TenantContext,
    _: OwnerOnly,
):
    location = Location(
        clinic_id=ctx.clinic_id,
        name=body.name,
        address=body.address,
        google_maps_url=body.google_maps_url,
        rooms=body.rooms,
    )
    ctx.db.add(location)
    await ctx.db.commit()
    await ctx.db.refresh(location)
    return location


@router.put("/{location_id}", response_model=LocationResponse)
@limiter.limit("100/minute")
async def update_location(
    request: Request,
    location_id: uuid.UUID,
    body: LocationUpdate,
    ctx: TenantContext,
    _: OwnerOnly,
):
    result = await ctx.db.execute(
        select(Location).where(
            Location.clinic_id == ctx.clinic_id,
            Location.id == location_id,
        )
    )
    location = result.scalar_one_or_none()
    if location is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Location not found.")

    updates = body.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(location, field, value)

    await ctx.db.commit()
    await ctx.db.refresh(location)
    return location


@router.delete("/{location_id}", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("100/minute")
async def delete_location(
    request: Request,
    location_id: uuid.UUID,
    ctx: TenantContext,
    _: OwnerOnly,
):
    result = await ctx.db.execute(
        select(Location).where(
            Location.clinic_id == ctx.clinic_id,
            Location.id == location_id,
        )
    )
    location = result.scalar_one_or_none()
    if location is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Location not found.")

    await ctx.db.delete(location)
    await ctx.db.commit()
