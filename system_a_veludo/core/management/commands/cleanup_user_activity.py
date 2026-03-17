from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count
from django.utils import timezone

from core.models import BlockedIP, UserActivity


class Command(BaseCommand):
    help = "One-click cleanup for UserActivity logs (by IP/time/all) with optional auto-ban."

    def add_arguments(self, parser):
        parser.add_argument("--ip", type=str, help="Delete logs for specific IP")
        parser.add_argument("--hours", type=int, default=None, help="Only delete records within recent N hours")
        parser.add_argument("--all", action="store_true", help="Delete all UserActivity records")
        parser.add_argument("--ban-ip", action="store_true", help="When using --ip, add the IP to BlockedIP")
        parser.add_argument("--auto-bot", action="store_true", help="Auto detect high-frequency IPs and delete/ban")
        parser.add_argument("--window-minutes", type=int, default=60, help="Time window for --auto-bot")
        parser.add_argument("--min-count", type=int, default=200, help="Minimum records in window to treat IP as bot")
        parser.add_argument("--dry-run", action="store_true", help="Preview only, do not delete")

    def handle(self, *args, **options):
        target_ip = options.get("ip")
        hours = options.get("hours")
        delete_all = options.get("all")
        ban_ip = options.get("ban_ip")
        auto_bot = options.get("auto_bot")
        window_minutes = options.get("window_minutes")
        min_count = options.get("min_count")
        dry_run = options.get("dry_run")

        if not any([target_ip, delete_all, auto_bot]):
            raise CommandError("Provide at least one mode: --ip / --all / --auto-bot")

        if delete_all and target_ip:
            raise CommandError("Use either --all or --ip, not both")

        if min_count <= 0:
            raise CommandError("--min-count must be > 0")

        now = timezone.now()
        start_time = None
        if hours is not None:
            if hours <= 0:
                raise CommandError("--hours must be > 0")
            start_time = now - timedelta(hours=hours)

        total_deleted = 0

        if delete_all:
            qs = UserActivity.objects.all()
            if start_time is not None:
                qs = qs.filter(timestamp__gte=start_time)

            count = qs.count()
            self.stdout.write(self.style.WARNING(f"[ALL] matched={count} dry_run={dry_run}"))
            if not dry_run:
                deleted, _ = qs.delete()
                total_deleted += deleted

        if target_ip:
            qs = UserActivity.objects.filter(meta_data__ip=target_ip)
            if start_time is not None:
                qs = qs.filter(timestamp__gte=start_time)

            count = qs.count()
            self.stdout.write(self.style.WARNING(f"[IP] ip={target_ip} matched={count} dry_run={dry_run}"))

            if not dry_run:
                deleted, _ = qs.delete()
                total_deleted += deleted

                if ban_ip:
                    blocked_ip, created = BlockedIP.objects.get_or_create(
                        ip=target_ip,
                        defaults={
                            "reason": "Manual ban via cleanup_user_activity command",
                            "is_active": True,
                            "hit_count": count,
                        },
                    )
                    if not created:
                        blocked_ip.is_active = True
                        blocked_ip.reason = "Manual ban via cleanup_user_activity command"
                        blocked_ip.hit_count = max(blocked_ip.hit_count, count)
                        blocked_ip.save(update_fields=["is_active", "reason", "hit_count", "last_detected_at"])

        if auto_bot:
            window_start = now - timedelta(minutes=window_minutes)
            candidates = (
                UserActivity.objects.filter(timestamp__gte=window_start)
                .exclude(meta_data__ip__isnull=True)
                .exclude(meta_data__ip="")
                .values("meta_data__ip")
                .annotate(count=Count("id"))
                .filter(count__gte=min_count)
                .order_by("-count")
            )

            candidate_list = list(candidates)
            self.stdout.write(
                self.style.WARNING(
                    f"[AUTO_BOT] window_minutes={window_minutes} min_count={min_count} matched_ips={len(candidate_list)} dry_run={dry_run}"
                )
            )

            for row in candidate_list:
                ip = row["meta_data__ip"]
                ip_count = row["count"]
                qs = UserActivity.objects.filter(meta_data__ip=ip, timestamp__gte=window_start)

                if dry_run:
                    self.stdout.write(f"  - {ip}: {ip_count}")
                    continue

                deleted, _ = qs.delete()
                total_deleted += deleted

                blocked_ip, created = BlockedIP.objects.get_or_create(
                    ip=ip,
                    defaults={
                        "reason": f"Auto ban by cleanup_user_activity (count={ip_count}/{window_minutes}m)",
                        "is_active": True,
                        "hit_count": ip_count,
                    },
                )
                if not created:
                    blocked_ip.is_active = True
                    blocked_ip.reason = f"Auto ban by cleanup_user_activity (count={ip_count}/{window_minutes}m)"
                    blocked_ip.hit_count = max(blocked_ip.hit_count, ip_count)
                    blocked_ip.save(update_fields=["is_active", "reason", "hit_count", "last_detected_at"])

                self.stdout.write(self.style.SUCCESS(f"  - {ip}: deleted={deleted}, banned=True"))

        if dry_run:
            self.stdout.write(self.style.SUCCESS("Dry run completed."))
        else:
            self.stdout.write(self.style.SUCCESS(f"Cleanup completed. total_deleted={total_deleted}"))
