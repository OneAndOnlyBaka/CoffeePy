import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("dashboard", "0009_inventorypolicy_mode"),
    ]

    operations = [
        migrations.RenameField(
            model_name="inventorypolicy",
            old_name="price_margin_percent",
            new_name="price_adjustment_percent",
        ),
        migrations.AlterField(
            model_name="inventorypolicy",
            name="price_adjustment_percent",
            field=models.SmallIntegerField(
                default=10,
                validators=[
                    django.core.validators.MinValueValidator(-100),
                    django.core.validators.MaxValueValidator(100),
                ],
            ),
        ),
    ]
