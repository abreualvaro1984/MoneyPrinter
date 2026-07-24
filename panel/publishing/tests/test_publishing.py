from __future__ import annotations

from django.test import TestCase

from panel.jobs.models import Job
from panel.niches.models import Niche
from panel.publishing.catalog import get_platform, list_platforms, required_field_keys
from panel.publishing.connectors.base import validate_metadata
from panel.publishing.models import PublishTarget, SocialAccount


class CatalogTests(TestCase):
    def test_platforms_include_br_monetized(self):
        ids = {p.id for p in list_platforms()}
        self.assertEqual(ids, {"youtube", "tiktok", "instagram", "facebook", "kwai"})
        for p in list_platforms():
            self.assertTrue(p.pays_per_view_br)
            self.assertTrue(p.monetization_notes)

    def test_youtube_required_fields(self):
        self.assertEqual(
            set(required_field_keys("youtube")),
            {"title", "description", "privacy"},
        )

    def test_validate_metadata_aliases(self):
        missing = validate_metadata(
            "instagram",
            {"description": "Legenda do reel", "privacy": "public"},
        )
        self.assertNotIn("caption", missing)


class SocialAccountTests(TestCase):
    def setUp(self):
        self.niche = Niche.objects.create(name="Finanças", slug="financas")

    def test_multiple_accounts_same_platform(self):
        a1 = SocialAccount.objects.create(
            name="YT Finanças A",
            platform=SocialAccount.Platform.YOUTUBE,
            niche=self.niche,
            status=SocialAccount.Status.CONNECTED,
            credentials_json='{"refresh_token":"x"}',
        )
        a2 = SocialAccount.objects.create(
            name="YT Finanças B",
            platform=SocialAccount.Platform.YOUTUBE,
            niche=self.niche,
            status=SocialAccount.Status.CONNECTED,
            credentials_json='{"refresh_token":"y"}',
        )
        self.assertEqual(
            SocialAccount.objects.filter(platform="youtube").count(),
            2,
        )
        self.assertTrue(a1.is_ready)
        self.assertTrue(a2.is_ready)

    def test_publish_target_metadata(self):
        account = SocialAccount.objects.create(
            name="TikTok Curiosidades",
            platform=SocialAccount.Platform.TIKTOK,
            auth_mode=SocialAccount.AuthMode.UPLOAD_POST,
            status=SocialAccount.Status.CONNECTED,
            credentials_json='{"api_key":"k","username":"u"}',
            default_privacy="PUBLIC_TO_EVERYONE",
        )
        job = Job.objects.create(
            niche=self.niche,
            job_type=Job.JobType.CREATE,
            subject="Tema teste",
            status=Job.Status.AWAITING_REVIEW,
            output_video="/tmp/fake.mp4",
            output_title="Titulo job",
        )
        target = PublishTarget.objects.create(
            job=job,
            account=account,
            title="Caption tiktok",
            hashtags="#financas, curiosidades",
            privacy="",
        )
        meta = target.to_metadata()
        self.assertEqual(meta["title"], "Caption tiktok")
        self.assertEqual(meta["privacy"], "PUBLIC_TO_EVERYONE")
        self.assertIn("#financas", meta["hashtags"])
        self.assertIn("#curiosidades", meta["hashtags"])
        self.assertEqual(get_platform("tiktok").id, "tiktok")
