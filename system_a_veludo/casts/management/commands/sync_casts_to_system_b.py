from django.core.management.base import BaseCommand

from casts.models import CastProfile
from casts.source import build_cast_profile_payload, build_cast_medias_payload
from utils.saas_client import SaaSClient


class Command(BaseCommand):
    help = "Sync local CastProfile/CastMedia from System A to System B resources API (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument("--only-active", action="store_true", help="Sync only active casts.")
        parser.add_argument("--limit", type=int, default=0, help="Limit records for dry rollout.")
        parser.add_argument("--dry-run", action="store_true", help="Preview without writing to System B.")

    def handle(self, *args, **options):
        only_active = options["only_active"]
        limit = int(options["limit"] or 0)
        dry_run = options["dry_run"]

        qs = CastProfile.objects.select_related("user").prefetch_related("medias").order_by("display_order", "id")
        if only_active:
            qs = qs.filter(is_active=True)
        if limit > 0:
            qs = qs[:limit]

        client = SaaSClient()
        total = 0
        success = 0
        failed = 0

        for cast in qs:
            total += 1
            user = cast.user
            display_name = cast.name or user.vrc_id or user.username
            profile_payload = build_cast_profile_payload(cast)
            medias_payload = build_cast_medias_payload(cast)

            self.stdout.write(f"[{total}] Sync cast={display_name} user_id={user.id}")
            if dry_run:
                self.stdout.write("  dry-run: skipped remote write")
                continue

            try:
                saas_id = client.sync_cast_to_saas(
                    user_id=user.id,
                    name=display_name,
                    email=user.email or "",
                    profile=profile_payload,
                    medias=medias_payload,
                )
                if saas_id:
                    if cast.saas_resource_id != saas_id:
                        cast.saas_resource_id = saas_id
                        cast.save(update_fields=["saas_resource_id"])
                    success += 1
                    self.stdout.write(self.style.SUCCESS(f"  ok -> saas_id={saas_id}"))
                else:
                    failed += 1
                    self.stdout.write(self.style.ERROR("  failed: empty saas_id"))
            except Exception as exc:
                failed += 1
                self.stdout.write(self.style.ERROR(f"  failed: {exc}"))

        summary = f"Sync finished. total={total}, success={success}, failed={failed}, dry_run={dry_run}"
        if failed:
            self.stdout.write(self.style.WARNING(summary))
        else:
            self.stdout.write(self.style.SUCCESS(summary))
