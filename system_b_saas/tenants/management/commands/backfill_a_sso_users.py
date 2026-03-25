from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from allauth.socialaccount.models import SocialAccount

from tenants.models import SaaSUser, Tenant


class Command(BaseCommand):
    help = "One-time backfill for historical System A SSO users: role mapping + veludo tenant binding."

    def add_arguments(self, parser):
        parser.add_argument(
            "--tenant-slug",
            default="veludo",
            help="Target tenant slug for A-origin users (default: veludo)",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Apply changes to database. Without this flag, runs in dry-run mode.",
        )
        parser.add_argument(
            "--admin-ids",
            default="",
            help="Comma separated B user IDs that must be forced to ADMIN (fallback/manual use).",
        )
        parser.add_argument(
            "--staff-ids",
            default="",
            help="Comma separated B user IDs that must be forced to STAFF (fallback/manual use).",
        )

    def _resolve_role(self, is_superuser: bool, is_staff: bool, is_cast: bool) -> str:
        if is_superuser or (is_staff and not is_cast):
            return "ADMIN"
        if is_cast:
            return "STAFF"
        return "CONSUMER"

    def _resolve_tenant(self, tenant_slug_or_name: str):
        tenant = Tenant.objects.filter(slug=tenant_slug_or_name).first()
        if tenant:
            return tenant

        tenant = Tenant.objects.filter(name__iexact=tenant_slug_or_name).first()
        if tenant:
            return tenant

        candidates = Tenant.objects.filter(slug__icontains=tenant_slug_or_name).order_by("name")
        if candidates.count() == 1:
            return candidates.first()

        return None

    def _parse_ids(self, raw_value: str):
        return {item.strip() for item in (raw_value or "").split(",") if item.strip()}

    def handle(self, *args, **options):
        tenant_slug = options["tenant_slug"].strip()
        apply_changes = bool(options["apply"])
        admin_ids = self._parse_ids(options.get("admin_ids", ""))
        staff_ids = self._parse_ids(options.get("staff_ids", ""))

        overlap = admin_ids.intersection(staff_ids)
        if overlap:
            raise CommandError(f"IDs cannot be in both --admin-ids and --staff-ids: {sorted(overlap)}")

        if not tenant_slug:
            raise CommandError("tenant slug cannot be empty")

        tenant = self._resolve_tenant(tenant_slug)
        if not tenant:
            raise CommandError(f"tenant '{tenant_slug}' not found by slug/name")

        table_names = set(connection.introspection.table_names())
        has_core_user = "core_user" in table_names

        rows = []
        mode_name = "core_user"
        if has_core_user:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT saas_user_id, is_superuser, is_staff, is_cast
                    FROM core_user
                    WHERE saas_user_id IS NOT NULL AND saas_user_id <> ''
                    """
                )
                rows = cursor.fetchall()
        else:
            mode_name = "fallback"
            self.stdout.write(self.style.WARNING("table 'core_user' not found; fallback mode will keep existing roles unless --admin-ids/--staff-ids is provided."))

            social_user_ids = set(
                SocialAccount.objects.filter(provider="discord").values_list("user_id", flat=True)
            )
            candidate_users = SaaSUser.objects.filter(id__in=social_user_ids).exclude(is_superuser=True)
            for user in candidate_users.iterator():
                rows.append((str(user.id), None, None, None))

        total = 0
        updated = 0
        skipped_missing_b_user = 0
        role_changed = 0
        staff_flag_changed = 0
        tenant_changed = 0

        missing_ids = []

        with transaction.atomic():
            for saas_user_id, a_is_superuser, a_is_staff, a_is_cast in rows:
                total += 1
                b_user = SaaSUser.objects.filter(id=saas_user_id).first()
                if not b_user:
                    skipped_missing_b_user += 1
                    if len(missing_ids) < 20:
                        missing_ids.append(str(saas_user_id))
                    continue

                user_id_str = str(b_user.id)
                if user_id_str in admin_ids:
                    desired_role = "ADMIN"
                elif user_id_str in staff_ids:
                    desired_role = "STAFF"
                elif has_core_user:
                    desired_role = self._resolve_role(bool(a_is_superuser), bool(a_is_staff), bool(a_is_cast))
                else:
                    desired_role = b_user.role if b_user.role in {"ADMIN", "STAFF", "CONSUMER"} else "CONSUMER"

                desired_is_staff = desired_role in {"ADMIN", "STAFF"}

                changed_fields = []

                if b_user.role != desired_role:
                    b_user.role = desired_role
                    changed_fields.append("role")
                    role_changed += 1

                if b_user.is_staff != desired_is_staff:
                    b_user.is_staff = desired_is_staff
                    changed_fields.append("is_staff")
                    staff_flag_changed += 1

                if b_user.tenant_id != tenant.id:
                    b_user.tenant = tenant
                    changed_fields.append("tenant")
                    tenant_changed += 1

                if changed_fields:
                    updated += 1
                    if apply_changes:
                        b_user.save(update_fields=changed_fields)

            if not apply_changes:
                transaction.set_rollback(True)

        mode = "APPLY" if apply_changes else "DRY-RUN"
        self.stdout.write(self.style.SUCCESS(f"[{mode}] Backfill completed"))
        self.stdout.write(f"Source mode: {mode_name}")
        self.stdout.write(f"A rows scanned: {total}")
        self.stdout.write(f"B users updated: {updated}")
        self.stdout.write(f" - role changed: {role_changed}")
        self.stdout.write(f" - is_staff changed: {staff_flag_changed}")
        self.stdout.write(f" - tenant changed: {tenant_changed}")
        self.stdout.write(f"Skipped (A has saas_user_id but B user missing): {skipped_missing_b_user}")

        if missing_ids:
            self.stdout.write("Sample missing B user ids: " + ", ".join(missing_ids))

        if not apply_changes:
            self.stdout.write(self.style.WARNING("Dry-run only. Re-run with --apply to persist."))
