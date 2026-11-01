from django.db import migrations, models


def enabled_to_mode(apps, schema_editor):
    # The switch had only one "on": members booking at the kiosk and on the phone.
    apps.get_model("dashboard", "InventoryPolicy").objects.filter(enabled=True).update(mode="members")


def mode_to_enabled(apps, schema_editor):
    apps.get_model("dashboard", "InventoryPolicy").objects.exclude(mode="off").update(enabled=True)


class Migration(migrations.Migration):

    dependencies = [
        ("dashboard", "0008_inventorypolicy_price_margin"),
    ]

    operations = [
        migrations.AddField(
            model_name="inventorypolicy",
            name="mode",
            field=models.CharField(
                choices=[
                    ("off", "Off"),
                    ("dashboard", "Only in the dashboard: one person keeps purchases and stock"),
                    ("members", "For members too: they pay in kind with purchases, at the kiosk and on their phone"),
                ],
                default="off",
                max_length=10,
            ),
        ),
        migrations.RunPython(enabled_to_mode, mode_to_enabled),
        migrations.RemoveField(model_name="inventorypolicy", name="enabled"),
    ]
