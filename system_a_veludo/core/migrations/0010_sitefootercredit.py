from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0009_user_discord_uid"),
    ]

    operations = [
        migrations.CreateModel(
            name="SiteFooterCredit",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "title",
                    models.TextField(
                        default="ASMR Salon Veludo-ビロード-は、VRChatクリエイターエコノミーセラーとして認定された、\nVRChat公認の商用利用店舗です。",
                        verbose_name="フッター説明文",
                    ),
                ),
                (
                    "credit_text",
                    models.TextField(
                        default="©Add+Re:collection　©ALICE　©Chocolate_rice　©HB_shop　©JOE　©P_Store　©SilverSpace　©Vagrant\n©#Ene_Collection　©あまとうさぎ　©かじや / kajiya　©さやぴ。　©ジンゴ　©ヤァ\nANMNMM　Atelier Astra　Eliya Workshop　EXTENSIONCLOTHING　FLASTORE　GLAYUnknown　KitsuZuri\nLys　Mister Pink　Nanaha　Ornament Corpse　PetiDoll　snowlight0102　Today cloth\nVAGRANT・Fermata Shop　YM STORE　ストレイ・ラム　にゃわて荘　はまのしす -hamanosis-　鴉屋さん",
                        verbose_name="クレジット表記",
                    ),
                ),
                (
                    "copyright_text",
                    models.CharField(default="© 2024 ASMR Salon Velode. All rights reserved.", max_length=200, verbose_name="コピーライト"),
                ),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "サイトフッタークレジット",
                "verbose_name_plural": "サイトフッタークレジット",
            },
        ),
    ]
