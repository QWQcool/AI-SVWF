"""Validated repository allowlist for Volcengine public virtual actors.

Clients submit only a catalog ``group_id``.  The provider-facing ``asset://``
URI is resolved here so an arbitrary trusted-person asset can never be injected
through the public API.
"""

import json
import re
from pathlib import Path
from typing import Any, Dict, Optional

from pydantic import ValidationError

from core.config import settings
from core.schemas import PublicVirtualActor


class PublicVirtualActorCatalogError(ValueError):
    """The repository catalog or a requested actor is invalid."""


class PublicVirtualActorCatalog:
    _catalog: Optional[Dict[str, Any]] = None
    _by_group_id: Dict[str, PublicVirtualActor] = {}
    _group_pattern = re.compile(r"^group-\d{14}-[a-z0-9]+$")
    _asset_pattern = re.compile(r"^asset://asset-\d{14}-[a-z0-9]+$")

    @classmethod
    def _path(cls) -> Path:
        return settings.PRESET_DIR / "public_virtual_actors.json"

    @classmethod
    def _load(cls) -> None:
        try:
            raw = json.loads(cls._path().read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PublicVirtualActorCatalogError(f"公共虚拟人白名单无法读取: {exc}") from exc
        if not isinstance(raw, dict):
            raise PublicVirtualActorCatalogError("公共虚拟人白名单根节点必须是对象")
        try:
            actors = [PublicVirtualActor.model_validate(item) for item in raw.get("actors", [])]
        except (TypeError, ValidationError) as exc:
            raise PublicVirtualActorCatalogError(f"公共虚拟人白名单字段非法: {exc}") from exc
        if not actors:
            raise PublicVirtualActorCatalogError("公共虚拟人白名单不能为空")

        by_group: Dict[str, PublicVirtualActor] = {}
        asset_uris: set[str] = set()
        for actor in actors:
            if not cls._group_pattern.fullmatch(actor.group_id):
                raise PublicVirtualActorCatalogError(f"非法公共虚拟人 group_id: {actor.group_id}")
            if not cls._asset_pattern.fullmatch(actor.asset_uri):
                raise PublicVirtualActorCatalogError(f"非法公共虚拟人 asset_uri: {actor.asset_uri}")
            if actor.group_id in by_group:
                raise PublicVirtualActorCatalogError(f"公共虚拟人 group_id 重复: {actor.group_id}")
            if actor.asset_uri in asset_uris:
                raise PublicVirtualActorCatalogError(f"公共虚拟人 asset_uri 重复: {actor.asset_uri}")
            if not actor.provider_public or not actor.project_allowlisted:
                raise PublicVirtualActorCatalogError(f"人物未启用公共白名单: {actor.group_id}")
            by_group[actor.group_id] = actor
            asset_uris.add(actor.asset_uri)

        default_group_id = str(raw.get("default_group_id") or "")
        if default_group_id not in by_group:
            raise PublicVirtualActorCatalogError("公共虚拟人默认 group_id 不在白名单中")

        cls._by_group_id = by_group
        cls._catalog = {
            "catalog_version": str(raw.get("catalog_version") or "1.0"),
            "provider": str(raw.get("provider") or "volcengine_ark"),
            "source": str(raw.get("source") or "volcengine_public_virtual_actor_library"),
            "default_group_id": default_group_id,
            "usage_notice": str(raw.get("usage_notice") or ""),
            "actors": actors,
        }

    @classmethod
    def catalog(cls) -> Dict[str, Any]:
        if cls._catalog is None:
            cls._load()
        assert cls._catalog is not None
        return {
            **cls._catalog,
            "actors": [actor.model_copy(deep=True) for actor in cls._catalog["actors"]],
        }

    @classmethod
    def default(cls) -> PublicVirtualActor:
        data = cls.catalog()
        return cls.require(str(data["default_group_id"]))

    @classmethod
    def get(cls, group_id: Optional[str]) -> Optional[PublicVirtualActor]:
        if not group_id:
            return None
        cls.catalog()
        actor = cls._by_group_id.get(group_id.strip())
        return actor.model_copy(deep=True) if actor else None

    @classmethod
    def require(cls, group_id: str) -> PublicVirtualActor:
        actor = cls.get(group_id)
        if not actor:
            raise PublicVirtualActorCatalogError(f"公共虚拟人不在项目白名单中: {group_id}")
        return actor

    @classmethod
    def require_asset_uri(cls, asset_uri: str) -> PublicVirtualActor:
        cls.catalog()
        for actor in cls._by_group_id.values():
            if actor.asset_uri == asset_uri:
                return actor.model_copy(deep=True)
        raise PublicVirtualActorCatalogError("公共虚拟人素材不在项目白名单中")
