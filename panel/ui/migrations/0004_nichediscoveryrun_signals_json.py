# Generated manually for NicheDiscoveryRun.signals_json

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("ui", "0003_nichediscoveryrun"),
    ]

    operations = [
        migrations.AddField(
            model_name="nichediscoveryrun",
            name="signals_json",
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text="Sinais brutos do YouTube (trending/buscas) usados na descoberta.",
            ),
        ),
    ]
