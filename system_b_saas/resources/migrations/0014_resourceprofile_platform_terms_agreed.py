from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("resources", "0013_servicepreset_description_servicepreset_price"),
    ]

    operations = [
        migrations.AddField(
            model_name="resourceprofile",
            name="platform_terms_agreed",
            field=models.BooleanField(default=False, verbose_name="プラットフォーム規約同意"),
        ),
    ]
