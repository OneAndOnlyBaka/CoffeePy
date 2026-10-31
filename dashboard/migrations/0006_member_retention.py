
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('coffee', '0011_backupsettings'),
        ('dashboard', '0005_kioskboards'),
    ]

    operations = [
        migrations.CreateModel(
            name='MemberRetention',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('departed_months', models.PositiveSmallIntegerField(default=0)),
            ],
        ),
        migrations.CreateModel(
            name='DeletionHold',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('reason', models.CharField(max_length=200)),
                ('held_by', models.CharField(blank=True, max_length=150)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('member', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='deletion_hold', to='coffee.member')),
            ],
        ),
    ]
