# Generated manually for ScriptDraft.target_duration_sec

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("ui", "0008_scriptdraft_llm_credential"),
    ]

    operations = [
        migrations.AddField(
            model_name="scriptdraft",
            name="target_duration_sec",
            field=models.PositiveIntegerField(
                default=60,
                help_text="Alvo falado do roteiro; o texto pode ficar uns segundos a mais ou a menos.",
                verbose_name="Duração alvo (segundos)",
            ),
        ),
    ]
