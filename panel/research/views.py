from __future__ import annotations

from django.contrib.admin.views.decorators import staff_member_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404

from panel.niches.models import Niche

from . import service


@staff_member_required
def run_research(request, niche_slug: str):
    niche = get_object_or_404(Niche, slug=niche_slug)
    snap = service.run_research_for_niche(niche)
    return JsonResponse(
        {
            "snapshot_id": snap.pk,
            "summary_pt": snap.summary_pt,
            "suggestions": snap.suggestions_json,
            "candidates_count": len(snap.candidates_json or []),
        }
    )
