# Generated manually for ScriptDraft.llm_credential

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("ui", "0007_videoplan"),
    ]

    operations = [
        migrations.AddField(
            model_name="scriptdraft",
            name="llm_credential",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="script_drafts",
                to="ui.llmcredential",
                verbose_name="IA usada",
            ),
        ),
    ]
