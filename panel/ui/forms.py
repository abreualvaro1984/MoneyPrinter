from __future__ import annotations

from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User

from panel.niches.models import Niche
from panel.publishing.catalog import PLATFORM_SPECS
from panel.ui.models import LlmCredential
from panel.ui.services.providers import provider_choices


PLATFORM_CHOICES = [(p.id, p.name) for p in PLATFORM_SPECS.values()]


class PanelRegisterForm(UserCreationForm):
    email = forms.EmailField(
        label="E-mail",
        required=False,
        help_text="Opcional",
    )

    class Meta:
        model = User
        fields = ("username", "email", "password1", "password2")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].label = "Usuário"
        self.fields["password1"].label = "Senha"
        self.fields["password2"].label = "Confirmar senha"
        self.fields["password1"].help_text = (
            "Mínimo 8 caracteres. Evite senhas óbvias ou só números."
        )
        self.fields["password2"].help_text = "Repita a senha."


class TrendSearchForm(forms.Form):
    niche = forms.ModelChoiceField(
        queryset=Niche.objects.filter(is_active=True),
        label="Nicho",
        empty_label="Selecione um nicho",
    )
    platforms = forms.MultipleChoiceField(
        choices=PLATFORM_CHOICES,
        label="Plataformas",
        widget=forms.CheckboxSelectMultiple,
        initial=["youtube"],
    )
    llm_credential = forms.ModelChoiceField(
        queryset=LlmCredential.objects.filter(is_active=True),
        label="IA para a pesquisa",
        required=False,
        empty_label="Padrão (credencial marcada como padrão / config.toml)",
    )


class LlmCredentialForm(forms.ModelForm):
    class Meta:
        model = LlmCredential
        fields = (
            "name",
            "provider",
            "api_key",
            "model_name",
            "base_url",
            "is_default",
            "is_active",
            "notes",
        )
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 3}),
            "api_key": forms.PasswordInput(render_value=True),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["provider"] = forms.ChoiceField(
            label="Provider",
            choices=provider_choices(),
        )
        self.fields["api_key"].help_text = "Armazenada no banco local do painel (não versionar)."
        self.fields["model_name"].help_text = "Opcional — vazio usa o default do provider."
        self.fields["base_url"].help_text = "Opcional — vazio usa o default do provider."


class ScriptGenerateForm(forms.Form):
    niche = forms.ModelChoiceField(
        queryset=Niche.objects.filter(is_active=True),
        label="Nicho",
    )
    topic = forms.CharField(label="Tema", max_length=300)
    trend_run_id = forms.IntegerField(required=False, widget=forms.HiddenInput)


class ScriptEditForm(forms.Form):
    title = forms.CharField(label="Título", max_length=200, required=False)
    body = forms.CharField(label="Roteiro", widget=forms.Textarea(attrs={"rows": 16}))
    hooks = forms.CharField(label="Hooks", widget=forms.Textarea(attrs={"rows": 3}), required=False)
    cta = forms.CharField(label="CTA", widget=forms.Textarea(attrs={"rows": 2}), required=False)
    hashtags = forms.CharField(label="Hashtags", max_length=500, required=False)
