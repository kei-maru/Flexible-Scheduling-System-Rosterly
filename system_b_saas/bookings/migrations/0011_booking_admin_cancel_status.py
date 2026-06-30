from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("bookings", "0010_booking_customer_name_labels"),
    ]

    operations = [
        migrations.AlterField(
            model_name="booking",
            name="status",
            field=models.CharField(
                choices=[
                    ("PENDING", "待确认"),
                    ("CONFIRMED", "已确认"),
                    ("CANCELLED", "已取消"),
                    ("CANCELLED_BY_ADMIN", "管理者キャンセル"),
                    ("COMPLETED", "完了"),
                ],
                default="CONFIRMED",
                max_length=20,
            ),
        ),
    ]
