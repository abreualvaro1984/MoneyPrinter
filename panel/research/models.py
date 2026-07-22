from __future__ import annotations

from django.db import models

from panel.niches.models import Niche


class ResearchSnapshot(models.Model):
    niche = models.ForeignKey(Niche, on_delete=models.CASCADE, related_name="research_snapshots")
    query = models.CharField(max_length=300)
    created_at = models.DateTimeField(auto_now_add=True)
    summary_pt = models.TextField(blank=True)
    suggestions_json = models.JSONField(default=dict, blank=True)
    candidates_json = models.JSONField(default=list, blank=True)

    class Meta:
        verbose_name = "Snapshot de pesquisa"
        verbose_name_plural = "Snapshots de pesquisa"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.niche.name} @ {self.created_at:%Y-%m-%d %H:%M}"
