"""Postgres-backed :class:`ExportPresetStore` (spec 2026-09-15 s1).

Hosted-mode counterpart to :class:`splitsmith.export_presets.JsonExportPresetStore`.
One row per (user, preset_id) in ``export_presets``.

**Multi-tenant invariant:** every statement filters on
``ExportPresetRow.user_id == self._user_id``. The composite primary key
enforces the boundary at the DB layer; the per-method filter enforces it
at the query layer. ``tests/test_export_presets_store.py`` has one
isolation test per method; add one for any new method.
"""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..export_presets import ExportPreset, ExportPresetBody, is_builtin_id
from .models import ExportPresetRow


class PostgresExportPresetStore:
    def __init__(self, session_factory: async_sessionmaker, *, user_id: str) -> None:
        if not isinstance(user_id, str) or not user_id:
            raise ValueError(
                "PostgresExportPresetStore requires a non-empty user_id; "
                f"got {user_id!r}. The auth layer must resolve a real "
                "user before constructing the per-request store."
            )
        self._session_factory = session_factory
        self._user_id = user_id

    async def list(self) -> list[ExportPreset]:
        async with self._session_factory() as session:
            stmt = select(ExportPresetRow).where(ExportPresetRow.user_id == self._user_id)
            rows = (await session.execute(stmt)).scalars().all()
        presets = [
            ExportPreset(
                preset_id=row.preset_id,
                name=row.name,
                updated_at=row.updated_at,
                body=ExportPresetBody.model_validate(row.body),
            )
            for row in rows
        ]
        return sorted(presets, key=lambda p: (p.name.casefold(), p.preset_id))

    async def put(self, preset: ExportPreset) -> None:
        if preset.builtin or is_builtin_id(preset.preset_id):
            raise ValueError(f"built-in preset {preset.preset_id!r} cannot be stored")
        async with self._session_factory() as session:
            existing = (
                await session.execute(
                    select(ExportPresetRow).where(
                        ExportPresetRow.user_id == self._user_id,
                        ExportPresetRow.preset_id == preset.preset_id,
                    )
                )
            ).scalar_one_or_none()
            body = preset.body.model_dump(mode="json")
            if existing is None:
                session.add(
                    ExportPresetRow(
                        user_id=self._user_id,
                        preset_id=preset.preset_id,
                        name=preset.name,
                        body=body,
                        updated_at=preset.updated_at,
                    )
                )
            else:
                existing.name = preset.name
                existing.body = body
                existing.updated_at = preset.updated_at
            await session.commit()

    async def delete(self, preset_id: str) -> None:
        async with self._session_factory() as session:
            await session.execute(
                delete(ExportPresetRow).where(
                    ExportPresetRow.user_id == self._user_id,
                    ExportPresetRow.preset_id == preset_id,
                )
            )
            await session.commit()
