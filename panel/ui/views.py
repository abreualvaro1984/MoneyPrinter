from __future__ import annotations

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from panel.niches.models import Niche
from panel.publishing.models import SocialAccount
from panel.ui.forms import (
    LlmCredentialForm,
    NicheDiscoverForm,
    PanelRegisterForm,
    ScriptEditForm,
    ScriptGenerateForm,
    SocialAccountForm,
    TrendSearchForm,
)
from panel.ui.models import LlmCredential, NicheDiscoveryRun, ScriptDraft, TrendRun
from panel.ui.services import niches_discover as niche_service
from panel.ui.services import scripts as script_service
from panel.ui.services import trends as trends_service
from panel.ui.services.account_tutorials import ACCOUNT_TUTORIALS, tutorial_for
from panel.ui.services import llm_test as llm_test_service
from panel.ui.services.llm_runtime import resolve_credential
from panel.ui.services.providers import PANEL_LLM_PRESETS


def _seo(title: str, description: str) -> dict:
    return {
        "page_title": title,
        "meta_description": description,
    }


@require_http_methods(["GET", "POST"])
def register(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        return redirect("ui:home")
    form = PanelRegisterForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user)
        messages.success(request, f"Conta criada. Bem-vindo, {user.username}!")
        return redirect("ui:home")
    return render(
        request,
        "registration/register.html",
        {
            **_seo("Cadastro — MoneyPrinter", "Crie sua conta no painel MoneyPrinter."),
            "form": form,
        },
    )


@login_required
def home(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "ui/home.html",
        {
            **_seo(
                "MoneyPrinter — Painel",
                "Fábrica multi-nicho: trends, roteiros, cortes e publicação.",
            ),
            "niche_count": Niche.objects.filter(is_active=True).count(),
            "trend_count": TrendRun.objects.count(),
            "script_count": ScriptDraft.objects.count(),
        },
    )


@login_required
def placeholder(request: HttpRequest, area: str) -> HttpResponse:
    labels = {
        "cortes": ("Cortes", "Cortes inteligentes de YouTube e arquivos locais (em breve)."),
        "create": ("Create", "Geração de vídeos com imagens no momento certo (em breve)."),
        "publicar": ("Publicar", "Destinos e upload multi-plataforma (use o Admin por enquanto)."),
    }
    title, desc = labels.get(area, ("Área", "Em construção."))
    return render(
        request,
        "ui/placeholder.html",
        {
            **_seo(f"{title} — MoneyPrinter", desc),
            "area_name": title,
            "area_blurb": desc,
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def trends_index(request: HttpRequest) -> HttpResponse:
    form = TrendSearchForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        cred = form.cleaned_data.get("llm_credential") or resolve_credential(None)
        run = trends_service.run_trends(
            form.cleaned_data["niche"],
            list(form.cleaned_data["platforms"]),
            llm_credential=cred,
        )
        messages.success(request, f"Trends #{run.pk} gerado para {run.niche}.")
        return redirect("ui:trends_detail", pk=run.pk)

    recent = TrendRun.objects.select_related("niche", "llm_credential")[:12]
    return render(
        request,
        "ui/trends_index.html",
        {
            **_seo(
                "Trends — MoneyPrinter",
                "Pesquise temas quentes por views e receba sugestão de adicionar ou pular.",
            ),
            "form": form,
            "recent": recent,
            "llm_count": LlmCredential.objects.filter(is_active=True).count(),
        },
    )


@login_required
def trends_detail(request: HttpRequest, pk: int) -> HttpResponse:
    run = get_object_or_404(
        TrendRun.objects.select_related("niche", "llm_credential"), pk=pk
    )
    return render(
        request,
        "ui/trends_detail.html",
        {
            **_seo(
                f"Trends #{run.pk} — MoneyPrinter",
                run.summary_pt[:160] if run.summary_pt else "Resultado de trends.",
            ),
            "run": run,
            "topics": run.topics_json or [],
        },
    )


@login_required
def apis_index(request: HttpRequest) -> HttpResponse:
    creds = LlmCredential.objects.all()
    return render(
        request,
        "ui/apis_index.html",
        {
            **_seo(
                "APIs de IA — MoneyPrinter",
                "Cadastre várias API keys e escolha qual usar nas pesquisas.",
            ),
            "credentials": creds,
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def apis_create(request: HttpRequest) -> HttpResponse:
    form = LlmCredentialForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "API de IA cadastrada.")
        return redirect("ui:apis_index")
    return render(
        request,
        "ui/apis_form.html",
        {
            **_seo("Nova API de IA — MoneyPrinter", "Cadastre só a API key."),
            "form": form,
            "page_heading": "Nova API de IA",
            "presets": PANEL_LLM_PRESETS,
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def apis_edit(request: HttpRequest, pk: int) -> HttpResponse:
    cred = get_object_or_404(LlmCredential, pk=pk)
    form = LlmCredentialForm(request.POST or None, instance=cred)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "API atualizada.")
        return redirect("ui:apis_index")
    return render(
        request,
        "ui/apis_form.html",
        {
            **_seo(f"Editar {cred.name} — MoneyPrinter", "Atualize a API key."),
            "form": form,
            "page_heading": f"Editar {cred.name}",
            "presets": PANEL_LLM_PRESETS,
        },
    )


@login_required
@require_POST
def apis_delete(request: HttpRequest, pk: int) -> HttpResponse:
    cred = get_object_or_404(LlmCredential, pk=pk)
    cred.delete()
    messages.info(request, "API removida.")
    return redirect("ui:apis_index")


@login_required
@require_POST
def apis_test(request: HttpRequest, pk: int) -> HttpResponse:
    """Testa uma credencial já salva com um prompt mínimo."""
    cred = get_object_or_404(LlmCredential, pk=pk)
    result = llm_test_service.test_llm_credential(cred)
    return render(
        request,
        "ui/partials/api_test_result.html",
        {"result": result},
    )


@login_required
@require_POST
def apis_test_live(request: HttpRequest) -> HttpResponse:
    """Testa provider + key do formulário (antes ou depois de salvar)."""
    result = llm_test_service.test_llm_draft(
        provider=request.POST.get("provider", ""),
        api_key=request.POST.get("api_key", ""),
    )
    return render(
        request,
        "ui/partials/api_test_result.html",
        {"result": result},
    )


@login_required
@require_POST
def trends_use_topic(request: HttpRequest, pk: int) -> HttpResponse:
    run = get_object_or_404(TrendRun, pk=pk)
    topic = (request.POST.get("topic") or "").strip()
    if not topic:
        messages.error(request, "Tema vazio.")
        return redirect("ui:trends_detail", pk=pk)
    draft = script_service.generate_script(run.niche, topic, trend_run=run)
    messages.success(request, f"Roteiro #{draft.pk} criado a partir do tema.")
    return redirect("ui:scripts_detail", pk=draft.pk)


@login_required
def scripts_index(request: HttpRequest) -> HttpResponse:
    drafts = ScriptDraft.objects.select_related("niche")[:40]
    form = ScriptGenerateForm()
    return render(
        request,
        "ui/scripts_index.html",
        {
            **_seo(
                "Roteiros — MoneyPrinter",
                "Gere e edite roteiros humanos. Não renderiza vídeo sozinho.",
            ),
            "drafts": drafts,
            "form": form,
        },
    )


@login_required
@require_http_methods(["POST"])
def scripts_generate(request: HttpRequest) -> HttpResponse:
    form = ScriptGenerateForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Preencha nicho e tema.")
        return redirect("ui:scripts_index")
    trend_run = None
    tid = form.cleaned_data.get("trend_run_id")
    if tid:
        trend_run = TrendRun.objects.filter(pk=tid).first()
    draft = script_service.generate_script(
        form.cleaned_data["niche"],
        form.cleaned_data["topic"],
        trend_run=trend_run,
    )
    messages.success(request, f"Roteiro #{draft.pk} gerado.")
    return redirect("ui:scripts_detail", pk=draft.pk)


@login_required
@require_http_methods(["GET", "POST"])
def scripts_detail(request: HttpRequest, pk: int) -> HttpResponse:
    draft = get_object_or_404(ScriptDraft.objects.select_related("niche", "trend_run"), pk=pk)
    if request.method == "POST":
        form = ScriptEditForm(request.POST)
        if form.is_valid():
            draft.title = form.cleaned_data["title"]
            draft.body = form.cleaned_data["body"]
            draft.hooks = form.cleaned_data["hooks"]
            draft.cta = form.cleaned_data["cta"]
            draft.hashtags = form.cleaned_data["hashtags"]
            draft.save()
            script_service.rescore(draft)
            messages.success(request, "Roteiro salvo e reavaliado.")
            return redirect("ui:scripts_detail", pk=draft.pk)
    else:
        form = ScriptEditForm(
            initial={
                "title": draft.title,
                "body": draft.body,
                "hooks": draft.hooks,
                "cta": draft.cta,
                "hashtags": draft.hashtags,
            }
        )
    return render(
        request,
        "ui/scripts_detail.html",
        {
            **_seo(
                f"{draft.title or draft.topic} — Roteiro",
                (draft.body or "")[:160],
            ),
            "draft": draft,
            "form": form,
        },
    )


@login_required
@require_POST
def scripts_regenerate(request: HttpRequest, pk: int) -> HttpResponse:
    draft = get_object_or_404(ScriptDraft, pk=pk)
    new = script_service.regenerate_script(draft)
    messages.success(request, f"Nova versão #{new.pk} (v{new.version}) gerada.")
    return redirect("ui:scripts_detail", pk=new.pk)


@login_required
@require_POST
def scripts_rescore(request: HttpRequest, pk: int) -> HttpResponse:
    draft = get_object_or_404(ScriptDraft, pk=pk)
    script_service.rescore(draft)
    messages.info(request, "Score anti-IA atualizado.")
    return redirect("ui:scripts_detail", pk=pk)


# --- Nichos (descoberta IA + cadastro SQLite) ---


@login_required
def niches_index(request: HttpRequest) -> HttpResponse:
    roots = Niche.objects.filter(parent__isnull=True, is_active=True).prefetch_related(
        "children"
    )
    latest = NicheDiscoveryRun.objects.filter(kind=NicheDiscoveryRun.Kind.ROOT).first()
    form = NicheDiscoverForm()
    return render(
        request,
        "ui/niches_index.html",
        {
            **_seo(
                "Nichos — MoneyPrinter",
                "A IA sugere nichos quentes; você adiciona os que quiser no SQLite.",
            ),
            "roots": roots,
            "latest": latest,
            "form": form,
        },
    )


@login_required
@require_POST
def niches_discover(request: HttpRequest) -> HttpResponse:
    form = NicheDiscoverForm(request.POST)
    cred = None
    if form.is_valid():
        cred = form.cleaned_data.get("llm_credential") or resolve_credential(None)
    else:
        cred = resolve_credential(None)
    run = niche_service.discover_root_niches(llm_credential=cred)
    messages.success(request, f"Descoberta #{run.pk}: {len(run.suggestions_json)} nichos sugeridos.")
    return redirect("ui:niches_discovery", pk=run.pk)


@login_required
def niches_discovery(request: HttpRequest, pk: int) -> HttpResponse:
    run = get_object_or_404(
        NicheDiscoveryRun.objects.select_related("parent_niche", "llm_credential"),
        pk=pk,
    )
    return render(
        request,
        "ui/niches_discovery.html",
        {
            **_seo(
                f"Descoberta #{run.pk} — MoneyPrinter",
                run.summary_pt[:160] if run.summary_pt else "Sugestões de nichos.",
            ),
            "run": run,
            "suggestions": run.suggestions_json or [],
        },
    )


@login_required
@require_POST
def niches_add_suggestion(request: HttpRequest, pk: int) -> HttpResponse:
    run = get_object_or_404(NicheDiscoveryRun, pk=pk)
    idx = request.POST.get("index")
    try:
        item = (run.suggestions_json or [])[int(idx)]
    except (TypeError, ValueError, IndexError):
        messages.error(request, "Sugestão inválida.")
        return redirect("ui:niches_discovery", pk=pk)
    niche = niche_service.add_suggestion_as_niche(
        name=item.get("name", ""),
        why=item.get("why", ""),
        keywords=item.get("keywords") or [],
        parent=run.parent_niche,
    )
    messages.success(request, f"Nicho salvo: {niche}")
    if run.kind == NicheDiscoveryRun.Kind.ROOT:
        return redirect("ui:niches_detail", pk=niche.pk)
    return redirect("ui:niches_detail", pk=run.parent_niche_id or niche.pk)


@login_required
def niches_detail(request: HttpRequest, pk: int) -> HttpResponse:
    niche = get_object_or_404(Niche.objects.prefetch_related("children"), pk=pk)
    latest_sub = (
        NicheDiscoveryRun.objects.filter(
            kind=NicheDiscoveryRun.Kind.SUB, parent_niche=niche
        ).first()
    )
    form = NicheDiscoverForm()
    return render(
        request,
        "ui/niches_detail.html",
        {
            **_seo(f"{niche.name} — Nicho", niche.briefing[:160] if niche.briefing else ""),
            "niche": niche,
            "subniches": niche.children.filter(is_active=True),
            "latest_sub": latest_sub,
            "form": form,
        },
    )


@login_required
@require_POST
def niches_discover_subs(request: HttpRequest, pk: int) -> HttpResponse:
    niche = get_object_or_404(Niche, pk=pk)
    form = NicheDiscoverForm(request.POST)
    cred = resolve_credential(None)
    if form.is_valid():
        cred = form.cleaned_data.get("llm_credential") or cred
    run = niche_service.discover_subniches(niche, llm_credential=cred)
    messages.success(request, f"{len(run.suggestions_json)} subnichos sugeridos.")
    return redirect("ui:niches_discovery", pk=run.pk)


# --- Contas sociais + tutorial ---


@login_required
def accounts_index(request: HttpRequest) -> HttpResponse:
    accounts = SocialAccount.objects.select_related("niche").all()
    return render(
        request,
        "ui/accounts_index.html",
        {
            **_seo(
                "Contas — MoneyPrinter",
                "Cadastre contas por plataforma com tutorial passo a passo.",
            ),
            "accounts": accounts,
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def accounts_create(request: HttpRequest) -> HttpResponse:
    form = SocialAccountForm(request.POST or None)
    platform = (request.POST.get("platform") or request.GET.get("platform") or "youtube").strip()
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        if obj.credentials_json and obj.status == SocialAccount.Status.DRAFT:
            obj.status = SocialAccount.Status.CONNECTED
        obj.save()
        messages.success(request, f"Conta {obj} salva no SQLite.")
        return redirect("ui:accounts_index")
    if form.is_bound and form.data.get("platform"):
        platform = form.data.get("platform")
    return render(
        request,
        "ui/accounts_form.html",
        {
            **_seo("Nova conta — MoneyPrinter", "Tutorial + cadastro de conta social."),
            "form": form,
            "page_heading": "Nova conta social",
            "tutorial": tutorial_for(platform),
            "tutorials_json": ACCOUNT_TUTORIALS,
            "selected_platform": platform,
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def accounts_edit(request: HttpRequest, pk: int) -> HttpResponse:
    account = get_object_or_404(SocialAccount, pk=pk)
    form = SocialAccountForm(request.POST or None, instance=account)
    platform = account.platform
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Conta atualizada.")
        return redirect("ui:accounts_index")
    if form.is_bound and form.data.get("platform"):
        platform = form.data.get("platform")
    return render(
        request,
        "ui/accounts_form.html",
        {
            **_seo(f"Editar {account.name} — MoneyPrinter", "Atualize a conta social."),
            "form": form,
            "page_heading": f"Editar {account.name}",
            "tutorial": tutorial_for(platform),
            "tutorials_json": ACCOUNT_TUTORIALS,
            "selected_platform": platform,
        },
    )


@login_required
@require_POST
def accounts_delete(request: HttpRequest, pk: int) -> HttpResponse:
    account = get_object_or_404(SocialAccount, pk=pk)
    account.delete()
    messages.info(request, "Conta removida.")
    return redirect("ui:accounts_index")
