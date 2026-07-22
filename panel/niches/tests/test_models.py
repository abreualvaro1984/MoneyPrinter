from __future__ import annotations

from django.test import TestCase

from panel.channels.models import YouTubeChannel
from panel.jobs.models import Job
from panel.niches.models import Niche


class NicheFactoryTests(TestCase):
    def test_niche_slug_and_channel(self):
        niche = Niche.objects.create(
            name="Curiosidades BR",
            keywords="curiosidades\nciencia",
            default_voice="pt-BR-AntonioNeural-Male",
        )
        self.assertEqual(niche.slug, "curiosidades-br")
        self.assertEqual(niche.keyword_list(), ["curiosidades", "ciencia"])
        channel = YouTubeChannel.objects.create(niche=niche)
        self.assertFalse(channel.is_ready)

    def test_create_job_draft(self):
        niche = Niche.objects.create(name="Tech BR")
        job = Job.objects.create(
            niche=niche,
            job_type=Job.JobType.CREATE,
            subject="Como funciona Wi-Fi",
            status=Job.Status.DRAFT,
        )
        path = job.ensure_work_dir()
        self.assertTrue(path.exists())
        self.assertIn(niche.slug, str(path))
