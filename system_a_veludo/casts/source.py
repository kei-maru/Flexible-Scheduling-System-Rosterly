from dataclasses import dataclass
from types import SimpleNamespace

from django.conf import settings
from django.urls import reverse

from casts.models import CastProfile
from utils.saas_client import SaaSClient


def use_remote_cast_source():
    return str(getattr(settings, "CAST_SOURCE", "remote")).lower() == "remote"


def allow_local_fallback():
    return bool(getattr(settings, "CAST_SOURCE_FALLBACK_LOCAL", True))


def skip_local_link():
    return bool(getattr(settings, "CAST_SOURCE_SKIP_LOCAL_LINK", False))


def require_numeric_external_id():
    return bool(getattr(settings, "CAST_SOURCE_REQUIRE_NUMERIC_EXTERNAL_ID", False))


def _safe_tags(tags):
    if isinstance(tags, list):
        return tags
    if isinstance(tags, str) and tags.strip():
        return [tags.strip()]
    return []


def _is_numeric_external_id(value):
    return str(value or "").strip().isdigit()


def _intro_appeal_from_intro(intro):
    text = intro or ""
    marker = "【アピール】"
    if marker in text:
        after_marker = text.split(marker, 1)[1]
        if "【" in after_marker:
            after_marker = after_marker.split("【", 1)[0]
        return after_marker.strip()
    return ""


class _MediaListAdapter:
    def __init__(self, medias):
        self.all = medias


class RemoteCastAdapter:
    """
    Template-compatible object for cast_list/index/booking pages.
    """

    def __init__(self, row):
        profile = row.get("profile") or {}
        medias = profile.get("medias") or []

        self.id = str(row.get("id") or "")
        self.external_id = str(row.get("external_id") or "")
        self.linked_user_id = str(row.get("linked_user_id") or "")
        self.linked_user_discord_id = str(row.get("linked_user_discord_id") or "")
        self.name = row.get("name") or ""
        self.rank = (profile.get("metadata") or {}).get("rank", "REGULAR")
        self.intro = profile.get("intro") or ""
        self.tags = _safe_tags(profile.get("tags"))
        self.youtube_url = profile.get("youtube_url") or ""
        self.saas_resource_id = str(row.get("id") or "")
        self.allow_30_min = bool(profile.get("allow_30_min", False))
        self.allow_60_min = bool(profile.get("allow_60_min", True))
        self.allow_120_min = bool(profile.get("allow_120_min", False))
        self.is_active = bool(row.get("is_active", True))
        self.display_order = int(profile.get("display_order", 0) or 0)
        self.user = SimpleNamespace(
            id="",
            username=self.name or "",
            vrc_id=self.name or "",
        )
        self.local_cast_profile_id = None
        self.local_edit_user_id = None
        self.edit_url = ""

        avatar_url = profile.get("avatar_url")
        self.avatar = SimpleNamespace(url=avatar_url) if avatar_url else None

        media_items = []
        for idx, item in enumerate(medias):
            image_url = item.get("image_url")
            media_items.append(
                SimpleNamespace(
                    id=item.get("id", idx),
                    title=item.get("title", ""),
                    media_type=item.get("media_type", "IMAGE"),
                    image_file=SimpleNamespace(url=image_url) if image_url else None,
                    video_url=item.get("video_url"),
                    cover_image=SimpleNamespace(url=item.get("cover_url")) if item.get("cover_url") else None,
                    order=item.get("order", idx),
                )
            )
        self.medias = _MediaListAdapter(media_items)

    @property
    def intro_appeal(self):
        return _intro_appeal_from_intro(self.intro)

    def attach_local_profile(self, local_profile):
        self.local_cast_profile_id = local_profile.id
        self.local_edit_user_id = local_profile.user.id
        self.user = SimpleNamespace(
            id=local_profile.user.id,
            username=local_profile.user.username or "",
            vrc_id=local_profile.user.vrc_id or "",
        )
        self.edit_url = reverse("edit_cast_profile", args=[local_profile.user.id])


def get_local_casts_queryset(active_only=True):
    queryset = CastProfile.objects.all()
    if active_only:
        queryset = queryset.filter(is_active=True)
    return queryset.order_by("display_order").prefetch_related("medias")


def get_public_casts(active_only=True):
    """
    Default source for user-facing pages. Reads System B first, then falls back.
    """
    if use_remote_cast_source():
        try:
            client = SaaSClient()
            rows = client.get_resources(active_only=active_only)
            casts = []
            for row in rows:
                if not row.get("id"):
                    continue
                if require_numeric_external_id() and not _is_numeric_external_id(row.get("external_id")):
                    continue
                casts.append(RemoteCastAdapter(row))

            # Emergency-safe behavior: if local profile tables are missing/broken,
            # still return remote casts instead of failing the whole page.
            if casts and not skip_local_link():
                try:
                    local_profiles = list(CastProfile.objects.select_related("user").all())
                    local_by_saas_id = {
                        str(cp.saas_resource_id): cp
                        for cp in local_profiles
                        if cp.saas_resource_id
                    }
                    local_by_user_id = {
                        str(cp.user.id): cp
                        for cp in local_profiles
                    }
                    for cast in casts:
                        linked_profile = (
                            local_by_saas_id.get(str(cast.saas_resource_id))
                            or local_by_user_id.get(str(cast.external_id))
                        )
                        if linked_profile:
                            cast.attach_local_profile(linked_profile)
                except Exception as local_exc:
                    print(f"[CastSource] Local link skipped due to error: {local_exc}")

            casts.sort(key=lambda c: (c.display_order, c.name.lower()))
            if casts:
                return casts
        except Exception as exc:
            print(f"[CastSource] Remote fetch failed: {exc}")
        if not allow_local_fallback():
            return []

    return list(get_local_casts_queryset(active_only=active_only))


def build_cast_profile_payload(cast_profile):
    if not cast_profile:
        return {}
    return {
        "intro": cast_profile.intro or "",
        "tags": _safe_tags(cast_profile.tags),
        "avatar_url": cast_profile.avatar.url if cast_profile.avatar else "",
        "youtube_url": cast_profile.youtube_url or "",
        "display_order": cast_profile.display_order,
        "allow_30_min": bool(cast_profile.allow_30_min),
        "allow_60_min": bool(cast_profile.allow_60_min),
        "allow_120_min": bool(cast_profile.allow_120_min),
    }


def build_cast_medias_payload(cast_profile):
    if not cast_profile:
        return []
    payload = []
    for media in cast_profile.medias.all().order_by("order", "id"):
        payload.append(
            {
                "title": media.title or "",
                "media_type": media.media_type or "IMAGE",
                "image_url": media.image_file.url if media.image_file else "",
                "video_url": "",
                "cover_url": media.cover_image.url if media.cover_image else "",
                "order": media.order,
                "is_active": True,
            }
        )
    return payload


@dataclass
class CastSyncResult:
    total: int = 0
    success: int = 0
    failed: int = 0


def sync_cast_profile_to_system_b(cast_profile):
    """
    Push local CastProfile/CastMedia to System B and backfill saas_resource_id.
    Returns saas_id or None.
    """
    if not cast_profile or not getattr(cast_profile, "user", None):
        return None

    user = cast_profile.user
    display_name = cast_profile.name or user.vrc_id or user.username
    client = SaaSClient()

    saas_id = client.sync_cast_to_saas(
        user_id=user.id,
        name=display_name,
        email=user.email or "",
        is_active=bool(cast_profile.is_active),
        profile=build_cast_profile_payload(cast_profile),
        medias=build_cast_medias_payload(cast_profile),
    )
    if saas_id and cast_profile.saas_resource_id != saas_id:
        cast_profile.saas_resource_id = saas_id
        cast_profile.save(update_fields=["saas_resource_id"])
    return saas_id
