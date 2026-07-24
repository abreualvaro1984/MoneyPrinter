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
    VideoPlanCreateForm,
    VideoPlanEditForm,
    YoutubeDataApiKeyForm,
)
from panel.ui.models import (
    LlmCredential,
    NicheDiscoveryRun,
    ScriptDraft,
    TrendRun,
    VideoPlan,
    YoutubeDataApiKey,
)
from panel.ui.services import niches_discover as niche_service
from panel.ui.services import scripts as script_service
from panel.ui.services import trends as trends_service
from panel.ui.services import video_plans as plan_service
from panel.ui.services.account_tutorials import ACCOUNT_TUTORIALS, tutorial_for
from panel.ui.services import llm_test as llm_test_service
from panel.ui.services.llm_runtime import resolve_credential
from panel.ui.services.providers import PANEL_LLM_PRESETS
from panel.ui.services.video_formats import get_video_format


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
            "llm_credentials": LlmCredential.objects.filter(is_active=True),
            "duration_choices": ScriptGenerateForm.base_fields[
                "target_duration_sec"
            ].choices,
        },
    )


@login_required
def apis_index(request: HttpRequest) -> HttpResponse:
    creds = LlmCredential.objects.all()
    yt = YoutubeDataApiKey.get_solo()
    return render(
        request,
        "ui/apis_index.html",
        {
            **_seo(
                "APIs — MoneyPrinter",
                "Cadastre YouTube API key e keys de IA para pesquisas e roteiros.",
            ),
            "credentials": creds,
            "youtube_key": yt,
            "youtube_masked": yt.api_key_masked,
        },
    )


@login_required
@require_POST
def apis_youtube_save(request: HttpRequest) -> HttpResponse:
    yt = YoutubeDataApiKey.get_solo()
    form = YoutubeDataApiKeyForm(request.POST, instance=yt)
    if form.is_valid():
        form.save()
        if form.cleaned_data.get("api_key"):
            messages.success(request, "YouTube API key salva no banco.")
        else:
            messages.info(request, "YouTube API key removida.")
    else:
        for err in form.errors.get("api_key", form.errors.get("__all__", [])):
            messages.error(request, str(err))
    return redirect("ui:apis_index")


@login_required
@require_POST
def apis_youtube_test(request: HttpRequest) -> HttpResponse:
    """Valida a key do formulário (ou a salva no banco se o campo vier vazio)."""
    from panel.ui.services import youtube_test as youtube_test_service

    result = youtube_test_service.test_youtube_api_key(
        request.POST.get("api_key", "")
    )
    return render(
        request,
        "ui/partials/api_test_result.html",
        {"result": result},
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
        model_name=request.POST.get("model_name", ""),
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
    cred_id = request.POST.get("llm_credential") or None
    cred = resolve_credential(int(cred_id) if cred_id else None)
    draft = script_service.generate_script(
        run.niche,
        topic,
        trend_run=run,
        llm_credential=cred,
        target_duration_sec=int(request.POST.get("target_duration_sec") or 60),
    )
    messages.success(request, f"Roteiro #{draft.pk} criado a partir do tema.")
    return redirect("ui:scripts_detail", pk=draft.pk)


@login_required
def scripts_index(request: HttpRequest) -> HttpResponse:
    drafts = ScriptDraft.objects.select_related("niche", "llm_credential")[:40]
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
@require_POST
def scripts_suggest_topics(request: HttpRequest) -> HttpResponse:
    niche_raw = (request.POST.get("niche") or "").strip()
    niche = None
    if niche_raw.isdigit():
        niche = Niche.objects.filter(pk=int(niche_raw), is_active=True).first()
    if not niche:
        return render(
            request,
            "ui/partials/script_topic_suggestions.html",
            {"topics": [], "error": "Selecione um nicho antes de pedir sugestões."},
        )
    cred_raw = (request.POST.get("llm_credential") or "").strip()
    cred = resolve_credential(int(cred_raw) if cred_raw.isdigit() else None)
    try:
        topics = script_service.suggest_topics(niche, llm_credential=cred, count=5)
    except Exception as exc:
        return render(
            request,
            "ui/partials/script_topic_suggestions.html",
            {"topics": [], "error": f"Falha ao sugerir: {exc}"},
        )
    return render(
        request,
        "ui/partials/script_topic_suggestions.html",
        {"topics": topics, "error": ""},
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
    cred = form.cleaned_data.get("llm_credential") or resolve_credential(None)
    draft = script_service.generate_script(
        form.cleaned_data["niche"],
        form.cleaned_data["topic"],
        trend_run=trend_run,
        llm_credential=cred,
        target_duration_sec=form.cleaned_data.get("target_duration_sec") or 60,
    )
    messages.success(request, f"Roteiro #{draft.pk} gerado.")
    return redirect("ui:scripts_detail", pk=draft.pk)


@login_required
@require_http_methods(["GET", "POST"])
def scripts_detail(request: HttpRequest, pk: int) -> HttpResponse:
    draft = get_object_or_404(
        ScriptDraft.objects.select_related("niche", "trend_run", "llm_credential"),
        pk=pk,
    )
    if request.method == "POST":
        form = ScriptEditForm(request.POST)
        if form.is_valid():
            draft.title = form.cleaned_data["title"]
            draft.body = form.cleaned_data["body"]
            draft.hooks = form.cleaned_data["hooks"]
            draft.cta = form.cleaned_data["cta"]
            draft.hashtags = form.cleaned_data["hashtags"]
            if form.cleaned_data.get("target_duration_sec"):
                draft.target_duration_sec = form.cleaned_data["target_duration_sec"]
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
                "target_duration_sec": draft.target_duration_sec or 60,
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
            "llm_credentials": LlmCredential.objects.filter(is_active=True),
            "duration_choices": ScriptGenerateForm.base_fields[
                "target_duration_sec"
            ].choices,
        },
    )


@login_required
@require_POST
def scripts_regenerate(request: HttpRequest, pk: int) -> HttpResponse:
    draft = get_object_or_404(ScriptDraft, pk=pk)
    cred_id = request.POST.get("llm_credential") or None
    if "llm_credential" in request.POST:
        cred = resolve_credential(int(cred_id) if cred_id else None)
    else:
        cred = draft.llm_credential or resolve_credential(None)
    duration_raw = request.POST.get("target_duration_sec")
    duration = int(duration_raw) if duration_raw else None
    new = script_service.regenerate_script(
        draft, llm_credential=cred, target_duration_sec=duration
    )
    messages.success(request, f"Nova versão #{new.pk} (v{new.version}) gerada.")
    return redirect("ui:scripts_detail", pk=new.pk)


@login_required
@require_POST
def scripts_humanize(request: HttpRequest, pk: int) -> HttpResponse:
    draft = get_object_or_404(ScriptDraft, pk=pk)
    cred_id = request.POST.get("llm_credential") or None
    if "llm_credential" in request.POST:
        cred = resolve_credential(int(cred_id) if cred_id else None)
    else:
        cred = draft.llm_credential or resolve_credential(None)
    draft = script_service.humanize_for_anti_ai(draft, llm_credential=cred)
    messages.success(
        request,
        f"Texto humanizado e reavaliado (score agora: "
        f"{draft.ai_score if draft.ai_score is not None else '—'}).",
    )
    return redirect("ui:scripts_detail", pk=pk)


@login_required
@require_POST
def scripts_variant(request: HttpRequest, pk: int) -> HttpResponse:
    draft = get_object_or_404(ScriptDraft, pk=pk)
    try:
        target = int(request.POST.get("target_duration_sec") or 0)
    except (TypeError, ValueError):
        target = 0
    if target < 15:
        messages.error(request, "Escolha uma duração válida para a variante.")
        return redirect("ui:scripts_detail", pk=pk)
    cred_id = request.POST.get("llm_credential") or None
    if "llm_credential" in request.POST:
        cred = resolve_credential(int(cred_id) if cred_id else None)
    else:
        cred = draft.llm_credential or resolve_credential(None)
    new = script_service.create_duration_variant(
        draft, target_duration_sec=target, llm_credential=cred
    )
    messages.success(
        request,
        f"Variante #{new.pk} (~{new.target_duration_sec}s) criada a partir do #{draft.pk}.",
    )
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
    history = (
        NicheDiscoveryRun.objects.select_related("llm_credential", "parent_niche")
        .all()[:30]
    )
    latest = history[0] if history else None
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
            "history": history,
            "form": form,
        },
    )


@login_required
@require_POST
def niches_discover(request: HttpRequest) -> HttpResponse:
    form = NicheDiscoverForm(request.POST)
    cred = None
    video_format = "dark"
    if form.is_valid():
        cred = form.cleaned_data.get("llm_credential") or resolve_credential(None)
        video_format = form.cleaned_data.get("video_format") or "dark"
    else:
        cred = resolve_credential(None)
        video_format = request.POST.get("video_format") or "dark"
    try:
        run = niche_service.discover_root_niches(
            llm_credential=cred,
            video_format=video_format,
        )
    except Exception as exc:
        messages.error(request, f"Falha na descoberta de nichos: {exc}")
        return redirect("ui:nichos")
    messages.success(request, f"Descoberta #{run.pk}: {len(run.suggestions_json)} nichos sugeridos.")
    return redirect("ui:niches_discovery", pk=run.pk)


@login_required
def niches_discovery(request: HttpRequest, pk: int) -> HttpResponse:
    from panel.ui.services.video_formats import get_video_format

    run = get_object_or_404(
        NicheDiscoveryRun.objects.select_related("parent_niche", "llm_credential"),
        pk=pk,
    )
    fmt = get_video_format(run.video_format or "any")
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
            "signals": run.signals_json or {},
            "video_format": fmt,
        },
    )


@login_required
@require_POST
def niches_add_suggestion(request: HttpRequest, pk: int) -> HttpResponse:
    run = get_object_or_404(NicheDiscoveryRun, pk=pk)
    idx_raw = request.POST.get("index")
    try:
        idx = int(idx_raw)
        item = (run.suggestions_json or [])[idx]
    except (TypeError, ValueError, IndexError):
        if request.headers.get("HX-Request"):
            return HttpResponse("Sugestão inválida.", status=400)
        messages.error(request, "Sugestão inválida.")
        return redirect("ui:niches_discovery", pk=pk)

    # Já adicionado nesta descoberta → só devolve o estado
    existing_id = item.get("added_niche_id")
    if existing_id:
        niche = Niche.objects.filter(pk=existing_id).first()
        if niche and request.headers.get("HX-Request"):
            return render(
                request,
                "ui/partials/niche_suggestion_actions.html",
                {"niche": niche, "run": run, "index": idx},
            )
        if niche:
            return redirect("ui:niches_discovery", pk=pk)

    niche = niche_service.add_suggestion_as_niche(
        name=item.get("name", ""),
        why=item.get("why", ""),
        keywords=item.get("keywords") or [],
        parent=run.parent_niche,
    )
    suggestions = list(run.suggestions_json or [])
    if 0 <= idx < len(suggestions):
        suggestions[idx] = {
            **suggestions[idx],
            "added_niche_id": niche.pk,
            "added_name": niche.name,
        }
        run.suggestions_json = suggestions
        run.save(update_fields=["suggestions_json"])

    if request.headers.get("HX-Request"):
        return render(
            request,
            "ui/partials/niche_suggestion_actions.html",
            {"niche": niche, "run": run, "index": idx},
        )

    messages.success(request, f"Nicho salvo: {niche}")
    return redirect("ui:niches_discovery", pk=pk)


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
    video_format = "dark"
    if form.is_valid():
        cred = form.cleaned_data.get("llm_credential") or cred
        video_format = form.cleaned_data.get("video_format") or "dark"
    else:
        video_format = request.POST.get("video_format") or "dark"
    try:
        run = niche_service.discover_subniches(
            niche,
            llm_credential=cred,
            video_format=video_format,
        )
    except Exception as exc:
        messages.error(request, f"Falha na pesquisa de subnichos: {exc}")
        return redirect("ui:niches_detail", pk=niche.pk)
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


# --- Plano de vídeo ---


def _assets_to_text(assets: list) -> str:
    lines = []
    for a in assets or []:
        if not isinstance(a, dict):
            continue
        lines.append(
            " | ".join(
                [
                    str(a.get("kind") or "broll"),
                    str(a.get("query_or_brief") or ""),
                    str(a.get("why") or ""),
                ]
            )
        )
    return "\n".join(lines)


def _dubs_to_text(dubs: list) -> str:
    lines = []
    for d in dubs or []:
        if not isinstance(d, dict):
            continue
        lines.append(
            " | ".join(
                [
                    str(d.get("title") or ""),
                    str(d.get("url") or ""),
                    str(d.get("why") or ""),
                ]
            )
        )
    return "\n".join(lines)


def _parse_assets_text(raw: str) -> list[dict]:
    out = []
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split("|")]
        kind = parts[0] if parts else "broll"
        brief = parts[1] if len(parts) > 1 else ""
        why = parts[2] if len(parts) > 2 else ""
        if not brief:
            continue
        out.append(
            {
                "kind": kind[:40],
                "query_or_brief": brief[:240],
                "why": why[:300],
                "timing_hint": "",
            }
        )
    return out


def _parse_dubs_text(raw: str) -> list[dict]:
    out = []
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split("|")]
        title = parts[0] if parts else ""
        if not title:
            continue
        out.append(
            {
                "title": title[:200],
                "url": (parts[1] if len(parts) > 1 else "")[:400],
                "why": (parts[2] if len(parts) > 2 else "")[:300],
                "channel": "",
                "language": "en",
                "search_query": "",
            }
        )
    return out


@login_required
@require_http_methods(["GET", "POST"])
def plans_index(request: HttpRequest) -> HttpResponse:
    form = VideoPlanCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        cred = form.cleaned_data.get("llm_credential") or resolve_credential(None)
        try:
            plan = plan_service.create_plan(
                niche=form.cleaned_data["niche"],
                topic=form.cleaned_data.get("topic") or "",
                video_format=form.cleaned_data.get("video_format") or "dark",
                llm_credential=cred,
            )
        except Exception as exc:
            messages.error(request, f"Falha ao gerar plano: {exc}")
            return redirect("ui:plans_index")
        messages.success(request, f"Plano #{plan.pk} criado.")
        return redirect("ui:plans_detail", pk=plan.pk)

    plans = VideoPlan.objects.select_related("niche", "llm_credential").all()[:40]
    return render(
        request,
        "ui/plans_index.html",
        {
            **_seo(
                "Plano de vídeo — MoneyPrinter",
                "A IA planeja roteiro, assets, voz e ideias de dublagem. Você edita.",
            ),
            "form": form,
            "plans": plans,
        },
    )


@login_required
def plans_detail(request: HttpRequest, pk: int) -> HttpResponse:
    plan = get_object_or_404(
        VideoPlan.objects.select_related("niche", "llm_credential", "script_draft"),
        pk=pk,
    )
    fmt = get_video_format(plan.video_format or "dark")
    form = VideoPlanEditForm(
        initial={
            "title": plan.title,
            "topic": plan.topic,
            "script_body": plan.script_body,
            "voice_name": plan.voice_name,
            "voice_notes": plan.voice_notes,
            "assets_text": _assets_to_text(plan.assets_json or []),
            "dub_text": _dubs_to_text(plan.dub_suggestions_json or []),
            "status": plan.status,
        }
    )
    return render(
        request,
        "ui/plans_detail.html",
        {
            **_seo(
                f"Plano #{plan.pk} — MoneyPrinter",
                plan.title or plan.topic or "Plano de vídeo",
            ),
            "plan": plan,
            "form": form,
            "video_format": fmt,
            "summary": (plan.plan_json or {}).get("summary_pt") or "",
        },
    )


@login_required
@require_POST
def plans_save(request: HttpRequest, pk: int) -> HttpResponse:
    plan = get_object_or_404(VideoPlan, pk=pk)
    form = VideoPlanEditForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Não foi possível salvar o plano.")
        return redirect("ui:plans_detail", pk=pk)
    plan.title = form.cleaned_data.get("title") or ""
    plan.topic = form.cleaned_data.get("topic") or ""
    plan.script_body = form.cleaned_data.get("script_body") or ""
    plan.voice_name = form.cleaned_data.get("voice_name") or ""
    plan.voice_notes = form.cleaned_data.get("voice_notes") or ""
    plan.assets_json = _parse_assets_text(form.cleaned_data.get("assets_text") or "")
    plan.dub_suggestions_json = _parse_dubs_text(
        form.cleaned_data.get("dub_text") or ""
    )
    status = form.cleaned_data.get("status") or plan.status
    if status in {VideoPlan.Status.DRAFT, VideoPlan.Status.READY}:
        plan.status = status
    plan.save()
    messages.success(request, "Plano salvo.")
    return redirect("ui:plans_detail", pk=pk)


@login_required
@require_POST
def plans_regenerate(request: HttpRequest, pk: int) -> HttpResponse:
    plan = get_object_or_404(VideoPlan, pk=pk)
    try:
        new = plan_service.regenerate_plan(plan)
    except Exception as exc:
        messages.error(request, f"Falha ao regenerar: {exc}")
        return redirect("ui:plans_detail", pk=pk)
    messages.success(request, f"Novo plano #{new.pk} gerado.")
    return redirect("ui:plans_detail", pk=new.pk)


@login_required
@require_POST
def plans_to_script(request: HttpRequest, pk: int) -> HttpResponse:
    plan = get_object_or_404(VideoPlan, pk=pk)
    draft = plan_service.export_to_script_draft(plan)
    messages.success(request, f"Roteiro #{draft.pk} criado a partir do plano.")
    return redirect("ui:scripts_detail", pk=draft.pk)
