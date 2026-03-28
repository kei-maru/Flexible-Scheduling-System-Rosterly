from django.db.models import Q

from allauth.socialaccount.models import SocialAccount

from resources.models import Availability, RecurringPattern, Resource, ScheduleTemplate


def normalize_profile_text(value):
    if value is None:
        return ""
    text = str(value)
    text = text.replace("\\\\u000d\\\\u000a", "\n")
    text = text.replace("\\\\u000d", "\n")
    text = text.replace("\\\\u000a", "\n")
    text = text.replace("\\\\r\\\\n", "\n")
    text = text.replace("\\\\n", "\n")
    text = text.replace("\\u000d\\u000a", "\n")
    text = text.replace("\\u000d", "\n")
    text = text.replace("\\u000a", "\n")
    text = text.replace("\\r\\n", "\n")
    text = text.replace("\\n", "\n")
    text = text.replace("\r\n", "\n")
    text = text.replace("\r", "\n")
    return text.strip()


def _identity_keys(user):
    keys = []
    if user is None:
        return keys

    social_uid = (
        SocialAccount.objects.filter(user=user, provider="discord")
        .values_list("uid", flat=True)
        .first()
    )
    if social_uid:
        keys.append(str(social_uid).strip())

    discord_id = (getattr(user, "discord_id", "") or "").strip()
    if discord_id:
        keys.append(discord_id)

    # preserve order + unique
    seen = set()
    result = []
    for k in keys:
        if k and k not in seen:
            result.append(k)
            seen.add(k)
    return result


def _discord_uid(user):
    if user is None:
        return ""
    uid = (
        SocialAccount.objects.filter(user=user, provider="discord")
        .values_list("uid", flat=True)
        .first()
    )
    return str(uid).strip() if uid else ""


def _desired_resource_name(user):
    if user is None:
        return ""
    return (getattr(user, "username", "") or "").strip()


def migrate_staff_schedule_data(tenant, user, target_resource, identity_keys=None):
    if not tenant or not user or not target_resource:
        return

    keys = identity_keys or _identity_keys(user)
    match_q = Q(linked_user=user)
    if user.email:
        match_q |= Q(linked_user__isnull=True, email=user.email)
    if user.username and not user.email and not keys:
        match_q |= Q(linked_user__isnull=True, name=user.username)
    if keys:
        # Do not use internal SaaS user numeric id for external_id matching.
        # external_id comes from System A user id and numeric overlap can cause cross-user hijacking.
        # Migrate schedule data even if this source resource was historically linked to a wrong user.
        match_q |= Q(external_id__in=keys)

    sources = (
        Resource.objects.filter(tenant=tenant)
        .filter(match_q)
        .exclude(id=target_resource.id)
        .distinct()
    )

    for source in sources:
        Availability.objects.filter(resource=source).update(resource=target_resource)

        for pattern in RecurringPattern.objects.filter(resource=source):
            RecurringPattern.objects.update_or_create(
                resource=target_resource,
                day_of_week=pattern.day_of_week,
                start_time=pattern.start_time,
                end_time=pattern.end_time,
                defaults={
                    "valid_from": pattern.valid_from,
                    "valid_until": pattern.valid_until,
                },
            )
        RecurringPattern.objects.filter(resource=source).delete()

        for tpl in ScheduleTemplate.objects.filter(resource=source):
            ScheduleTemplate.objects.update_or_create(
                resource=target_resource,
                name=tpl.name,
                defaults={"week_config": tpl.week_config},
            )
        ScheduleTemplate.objects.filter(resource=source).delete()

        if source.linked_user_id == user.id:
            source.linked_user = None
            source.save(update_fields=["linked_user"])


def ensure_staff_resource_binding(user, tenant=None, allow_create=True):
    if user is None:
        return None
    if getattr(user, "role", "") not in {"STAFF", "ADMIN"}:
        return None

    tenant_obj = tenant or getattr(user, "tenant", None)
    if tenant_obj is None:
        return None

    keys = _identity_keys(user)
    discord_uid = _discord_uid(user)

    linked = Resource.objects.filter(tenant=tenant_obj, linked_user=user).first()
    if linked:
        update_fields = []
        desired_name = _desired_resource_name(user)
        if desired_name and linked.name != desired_name:
            linked.name = desired_name
            update_fields.append("name")
        if user.email and linked.email != user.email:
            linked.email = user.email
            update_fields.append("email")
        if linked.is_active != user.is_active:
            linked.is_active = user.is_active
            update_fields.append("is_active")

        # external_id belongs to System A identity namespace.
        # Do not auto-fill it from Discord identities in System B binding flows.
        if discord_uid and linked.external_id == discord_uid:
            linked.external_id = None
            update_fields.append("external_id")

        if update_fields:
            linked.save(update_fields=update_fields)
        migrate_staff_schedule_data(tenant_obj, user, linked, identity_keys=keys)
        return linked

    reusable = None
    if keys:
        reusable = Resource.objects.filter(
            tenant=tenant_obj,
            linked_user__isnull=True,
            external_id__in=keys,
        ).first()
    if reusable is None and user.email:
        reusable = Resource.objects.filter(
            tenant=tenant_obj,
            linked_user__isnull=True,
            email=user.email,
        ).first()
    if reusable is None and user.username:
        reusable = Resource.objects.filter(
            tenant=tenant_obj,
            linked_user__isnull=True,
            name=user.username,
        ).first()

    if reusable:
        reusable.linked_user = user
        update_fields = ["linked_user"]
        desired_name = _desired_resource_name(user)
        if desired_name and reusable.name != desired_name:
            reusable.name = desired_name
            update_fields.append("name")
        if user.email and reusable.email != user.email:
            reusable.email = user.email
            update_fields.append("email")
        if reusable.is_active != user.is_active:
            reusable.is_active = user.is_active
            update_fields.append("is_active")

        # Keep external_id reserved for System A IDs; clear legacy UID-shaped values.
        if discord_uid and reusable.external_id == discord_uid:
            reusable.external_id = None
            update_fields.append("external_id")

        reusable.save(update_fields=update_fields)
        migrate_staff_schedule_data(tenant_obj, user, reusable, identity_keys=keys)
        return reusable

    if not allow_create:
        return None

    base_name = _desired_resource_name(user) or (user.email or "").split("@")[0].strip() or f"staff-{user.id}"
    candidate = base_name[:90]
    suffix = 2
    while Resource.objects.filter(tenant=tenant_obj, name=candidate).exclude(linked_user=user).exists():
        candidate = f"{base_name[:80]}-{suffix}"
        suffix += 1

    created = Resource.objects.create(
        tenant=tenant_obj,
        linked_user=user,
        external_id=None,
        name=candidate,
        email=(user.email or "").strip() or None,
        is_active=user.is_active,
    )
    migrate_staff_schedule_data(tenant_obj, user, created, identity_keys=keys)
    return created
