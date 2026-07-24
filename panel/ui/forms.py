from __future__ import annotations

from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User

from panel.niches.models import Niche
from panel.publishing.catalog import PLATFORM_SPECS
from panel.publishing.models import SocialAccount
from panel.ui.models import LlmCredential, YoutubeDataApiKey
from panel.ui.services.providers import (
    apply_provider_defaults,
    get_panel_preset,
    models_catalog_json,
    panel_model_choices,
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


class YoutubeDataApiKeyForm(forms.ModelForm):
    """API key YouTube Data API v3 — digitada na UI do usuário."""

    class Meta:
        model = YoutubeDataApiKey
        fields = ("api_key",)
        widgets = {
            "api_key": forms.PasswordInput(
                render_value=True,
                attrs={
                    "placeholder": "AIza…",
                    "autocomplete": "off",
                    "spellcheck": "false",
                },
            )
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["api_key"].label = "YouTube API key"
        self.fields["api_key"].required = False
        self.fields["api_key"].help_text = (
            "YouTube Data API v3 (costuma começar com AIza…). "
            "Não use GOCSPX- (isso é client secret OAuth)."
        )

    def clean_api_key(self) -> str:
        key = (self.cleaned_data.get("api_key") or "").strip()
        if key.upper().startswith("GOCSPX"):
            raise forms.ValidationError(
                "Isso é client secret OAuth (GOCSPX-…), não API key. "
                "Em Google Cloud → Credenciais → Create credentials → API key."
            )
        return key


class LlmCredentialForm(forms.ModelForm):
    """Provider + modelo + API key (+ padrão). URL base vem do sistema."""

    model_name = forms.ChoiceField(
        label="Modelo",
        help_text="Lista muda conforme a IA. O teste usa o modelo selecionado.",
    )

    class Meta:
        model = LlmCredential
        fields = ("provider", "model_name", "api_key", "is_default")
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
        self.fields["api_key"].help_text = "Só a chave. A URL da API é preenchida automaticamente."
        self.fields["is_default"].label = "Usar como padrão nas pesquisas"

        current = self.initial.get("provider") or self.data.get("provider")
        if not current and self.instance and self.instance.pk:
            current = self.instance.provider
        if not current:
            current = panel_provider_choices()[0][0]
        current = str(current)

        extra_model = ""
        if self.data.get("model_name"):
            extra_model = str(self.data.get("model_name") or "").strip()
        elif self.instance and self.instance.pk:
            extra_model = (self.instance.model_name or "").strip()
        elif self.initial.get("model_name"):
            extra_model = str(self.initial.get("model_name") or "").strip()

        self.fields["model_name"].choices = panel_model_choices(
            current, extra=extra_model
        )
        if not self.is_bound:
            if extra_model:
                self.fields["model_name"].initial = extra_model
            else:
                choices = self.fields["model_name"].choices
                if choices:
                    self.fields["model_name"].initial = choices[0][0]

        preset = get_panel_preset(current)
        self.key_url = preset.key_url if preset else ""
        self.key_howto = preset.howto if preset else ""
        self.models_catalog_json = models_catalog_json()

    def save(self, commit=True):
        obj: LlmCredential = super().save(commit=False)
        provider = self.cleaned_data["provider"]
        preset = get_panel_preset(provider)
        defaults = apply_provider_defaults(provider)
        obj.provider = provider
        obj.model_name = (self.cleaned_data.get("model_name") or "").strip() or (
            defaults.get("model_name") or ""
        )
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
    video_format = forms.ChoiceField(
        label="Tipo de vídeo que você quer produzir",
        choices=(),  # preenchido no __init__
        initial="dark",
        help_text="Dark, dormir, tela preta, ambiente, aparecendo… A IA valida o fit de cada sugestão.",
    )
    llm_credential = forms.ModelChoiceField(
        queryset=LlmCredential.objects.filter(is_active=True),
        label="IA",
        required=False,
        empty_label="Padrão",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from panel.ui.services.video_formats import VIDEO_FORMAT_CHOICES

        self.fields["video_format"].choices = VIDEO_FORMAT_CHOICES



class ScriptGenerateForm(forms.Form):
    niche = forms.ModelChoiceField(
        queryset=Niche.objects.filter(is_active=True),
        label="Nicho",
    )
    topic = forms.CharField(label="Tema", max_length=300)
    target_duration_sec = forms.TypedChoiceField(
        label="Duração alvo do vídeo",
        coerce=int,
        choices=(
            (30, "30s — Short rápido (descoberta)"),
            (45, "45s — Short / Reels"),
            (60, "60s — mín. TikTok Creator Rewards / Short sólido"),
            (90, "90s — Short confortável"),
            (180, "3 min — teto típico YouTube Shorts"),
            (480, "8 min — mid-roll ads (vídeo longo YT)"),
            (600, "10 min — vídeo longo"),
        ),
        initial=60,
        help_text=(
            "O roteiro mira esse tempo falado (± alguns segundos ok). "
            "YouTube: Shorts até ~3 min (sem duração mínima p/ ads Shorts); "
            "mid-roll em vídeo longo costuma exigir 8+ min. "
            "TikTok Creator Rewards: em geral 60s+."
        ),
    )
    trend_run_id = forms.IntegerField(required=False, widget=forms.HiddenInput)
    llm_credential = forms.ModelChoiceField(
        queryset=LlmCredential.objects.filter(is_active=True),
        label="IA para o roteiro",
        required=False,
        empty_label="Padrão (credencial ★ / config.toml)",
        help_text="Independente da IA usada na pesquisa de nicho/trends.",
    )


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
    target_duration_sec = forms.IntegerField(
        label="Duração alvo (segundos)",
        min_value=15,
        max_value=3600,
        required=False,
        help_text="Só referência; use Regenerar para reescrever no novo tempo.",
    )


class VideoPlanCreateForm(forms.Form):
    niche = forms.ModelChoiceField(
        queryset=Niche.objects.filter(is_active=True),
        label="Nicho",
        empty_label="Selecione um nicho",
    )
    video_format = forms.ChoiceField(
        label="Tipo de vídeo",
        choices=(),
        initial="dark",
    )
    topic = forms.CharField(
        label="Tema (opcional)",
        max_length=300,
        required=False,
        help_text="Se vazio, a IA inventa um tema alinhado ao nicho.",
    )
    llm_credential = forms.ModelChoiceField(
        queryset=LlmCredential.objects.filter(is_active=True),
        label="IA",
        required=False,
        empty_label="Padrão",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from panel.ui.services.video_formats import VIDEO_FORMAT_CHOICES

        self.fields["video_format"].choices = VIDEO_FORMAT_CHOICES
        self.fields["niche"].queryset = Niche.objects.filter(is_active=True)


class VideoPlanEditForm(forms.Form):
    title = forms.CharField(label="Título", max_length=200, required=False)
    topic = forms.CharField(label="Tema", max_length=300, required=False)
    script_body = forms.CharField(
        label="Roteiro",
        widget=forms.Textarea(attrs={"rows": 14}),
        required=False,
    )
    voice_name = forms.CharField(label="Voz TTS", max_length=120, required=False)
    voice_notes = forms.CharField(
        label="Notas da voz",
        widget=forms.Textarea(attrs={"rows": 2}),
        required=False,
    )
    assets_text = forms.CharField(
        label="Assets (um por linha: kind | brief | why)",
        widget=forms.Textarea(attrs={"rows": 8}),
        required=False,
        help_text="Ex.: stock_video | chuva noturna | abertura",
    )
    dub_text = forms.CharField(
        label="Dublagens (um por linha: título | url | por quê)",
        widget=forms.Textarea(attrs={"rows": 6}),
        required=False,
    )
    status = forms.ChoiceField(
        label="Status",
        choices=(("draft", "Rascunho"), ("ready", "Pronto")),
        required=False,
    )
