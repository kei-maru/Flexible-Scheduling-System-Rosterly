from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils.text import slugify
import re
import secrets

from tenants.models import SaaSUser, Tenant


class Command(BaseCommand):
    help = "Create or update a local super admin account without Discord OAuth."

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True, help="Super admin username")
        parser.add_argument("--password", required=True, help="Super admin password")
        parser.add_argument("--email", default="", help="Super admin email")
        parser.add_argument(
            "--tenant-slug",
            default="",
            help="Optional default tenant slug for compatibility with shop admin/staff pages",
        )
        parser.add_argument(
            "--tenant-name",
            default="",
            help="Tenant name used when creating tenant automatically",
        )
        parser.add_argument(
            "--create-tenant-if-missing",
            action="store_true",
            help="Auto create tenant when --tenant-slug does not exist",
        )

    def _build_unique_tenant_slug(self, raw_slug: str) -> str:
        base_slug = slugify(raw_slug or "debug-shop") or "debug-shop"
        base_slug = re.sub(r"[^a-z0-9-]", "", base_slug.lower()).strip("-") or "debug-shop"
        candidate = base_slug
        idx = 2
        while Tenant.objects.filter(slug=candidate).exists():
            candidate = f"{base_slug}-{idx}"
            idx += 1
        return candidate

    def handle(self, *args, **options):
        username = (options.get("username") or "").strip()
        password = options.get("password") or ""
        email = (options.get("email") or "").strip()
        tenant_slug = (options.get("tenant_slug") or "").strip()
        tenant_name = (options.get("tenant_name") or "").strip()
        create_tenant_if_missing = bool(options.get("create_tenant_if_missing"))

        if not username:
            raise CommandError("--username is required")
        if len(password) < 8:
            raise CommandError("--password must be at least 8 characters")

        tenant = None
        if tenant_slug:
            tenant = Tenant.objects.filter(slug=tenant_slug).first() or Tenant.objects.filter(slug__iexact=tenant_slug).first()
            if not tenant:
                if not create_tenant_if_missing:
                    raise CommandError(
                        f"tenant slug not found: {tenant_slug}. Use --create-tenant-if-missing to auto create one."
                    )
                normalized_slug = self._build_unique_tenant_slug(tenant_slug)
                tenant = Tenant.objects.create(
                    name=tenant_name or tenant_slug,
                    slug=normalized_slug,
                    contact_email=email or None,
                    api_key=secrets.token_urlsafe(24)[:32],
                    api_secret=secrets.token_urlsafe(32),
                    is_api_enabled=True,
                    enable_saas_dashboard=True,
                )
                self.stdout.write(self.style.WARNING(f"tenant auto-created: {tenant.name} ({tenant.slug})"))

        with transaction.atomic():
            user = SaaSUser.objects.filter(username=username).first()
            created = user is None
            if created:
                user = SaaSUser(username=username)

            update_fields = []
            if email and user.email != email:
                user.email = email
                update_fields.append("email")
            if tenant and user.tenant_id != tenant.id:
                user.tenant = tenant
                update_fields.append("tenant")
            if user.role != "ADMIN":
                user.role = "ADMIN"
                update_fields.append("role")
            if not user.is_staff:
                user.is_staff = True
                update_fields.append("is_staff")
            if not user.is_superuser:
                user.is_superuser = True
                update_fields.append("is_superuser")
            if not user.is_active:
                user.is_active = True
                update_fields.append("is_active")

            user.set_password(password)
            if created:
                user.save()
            else:
                update_fields.append("password")
                user.save(update_fields=sorted(set(update_fields)))

        action = "created" if created else "updated"
        self.stdout.write(self.style.SUCCESS(f"Super admin {action}: {username} (id={user.id})"))
        self.stdout.write("Login entry: /dashboard/super/login/")
        if tenant:
            self.stdout.write(f"Default tenant attached: {tenant.slug}")
