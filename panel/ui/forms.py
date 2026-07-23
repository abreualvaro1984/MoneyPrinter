from __future__ import annotations

from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User

from panel.niches.models import Niche
from panel.publishing.catalog import PLATFORM_SPECS
from panel.publishing.models import SocialAccount
from panel.ui.models import LlmCredential
from panel.ui.services.providers import (
    apply_provider_defaults,
    get_panel_preset,
    panel_provider_choices,
)


PLATFORM_CHOICES = [(p.id, p.name) for p in PLATFORM_SPECS.values()]


class ProviderSelectWidget(forms.Select):
    """Cada opção carrega data-key-url para o link da página da API key."""

    def create_option(
        self, name, value, label, selected, index, subindex=None, attrs=None
    ):
        option = super().create_option(
            name, value, label, selected, index, subindex=subindex, attrs=attrs
        )
        preset = get_panel_preset(str(value)) if value else None
        if preset:
            option["attrs"]["data-key-url"] = preset.key_url
            option["attrs"]["data-howto"] = preset.howto
        return option


class PanelRegisterForm(UserCreationForm):
    email = forms.EmailField(label="E-mail", required=False, help_text="Opcional")

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
        empty_label="Padrão (credencial ★ / config.toml)",
    )


class LlmCredentialForm(forms.ModelForm):
    """Só provider + API key (+ padrão). URL e modelo vêm do sistema."""

    class Meta:
        model = LlmCredential
        fields = ("provider", "api_key", "is_default")
        widgets = {"api_key": forms.PasswordInput(render_value=True)}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["provider"] = forms.ChoiceField(
            label="IA",
            choices=panel_provider_choices(),
            widget=ProviderSelectWidget,
            help_text="Selecione a IA e abra o link abaixo para gerar/copiar a API key.",
        )
        self.fields["api_key"].label = "API key"
        self.fields["api_key"].help_text = "Só a chave. URL e modelo são preenchidos automaticamente."
        self.fields["is_default"].label = "Usar como padrão nas pesquisas"
        # URL inicial para o link dinâmico (primeira opção ou valor atual)
        current = self.initial.get("provider") or self.data.get("provider")
        if not current and self.instance and self.instance.pk:
            current = self.instance.provider
        if not current:
            current = panel_provider_choices()[0][0]
        preset = get_panel_preset(str(current))
        self.key_url = preset.key_url if preset else ""
        self.key_howto = preset.howto if preset else ""

    def save(self, commit=True):
        obj: LlmCredential = super().save(commit=False)
        provider = self.cleaned_data["provider"]
        preset = get_panel_preset(provider)
        defaults = apply_provider_defaults(provider)
        obj.provider = provider
        obj.model_name = defaults.get("model_name") or ""
        obj.base_url = defaults.get("base_url") or ""
        label = preset.label if preset else provider
        # Nome amigável automático (sem digitar)
        desired = label
        if obj.pk:
            # Mantém sufixo #N se já existir; senão usa o label do preset
            if not obj.name or not obj.name.startswith(label):
                desired = label
            else:
                desired = obj.name
        obj.name = desired
        base = label
        n = 2
        qs = LlmCredential.objects.filter(provider=provider, name=obj.name)
        if obj.pk:
            qs = qs.exclude(pk=obj.pk)
        while qs.exists():
            obj.name = f"{base} #{n}"
            qs = LlmCredential.objects.filter(provider=provider, name=obj.name)
            if obj.pk:
                qs = qs.exclude(pk=obj.pk)
            n += 1
        obj.is_active = True
        if commit:
            obj.save()
        return obj


class SocialAccountForm(forms.ModelForm):
    class Meta:
        model = SocialAccount
        fields = (
            "name",
            "platform",
            "niche",
            "username",
            "external_id",
            "auth_mode",
            "credentials_json",
            "default_privacy",
            "notes",
            "is_active",
        )
        widgets = {
            "credentials_json": forms.Textarea(attrs={"rows": 6}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["niche"].queryset = Niche.objects.filter(is_active=True)
        self.fields["niche"].required = False
        self.fields["credentials_json"].label = "Credenciais (JSON)"
        self.fields["auth_mode"].help_text = "Veja o tutorial ao lado conforme a plataforma."


class NicheDiscoverForm(forms.Form):
    llm_credential = forms.ModelChoiceField(
        queryset=LlmCredential.objects.filter(is_active=True),
        label="IA",
        required=False,
        empty_label="Padrão",
    )


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
    hooks = forms.CharField(
        label="Hooks", widget=forms.Textarea(attrs={"rows": 3}), required=False
    )
    cta = forms.CharField(
        label="CTA", widget=forms.Textarea(attrs={"rows": 2}), required=False
    )
    hashtags = forms.CharField(label="Hashtags", max_length=500, required=False)
