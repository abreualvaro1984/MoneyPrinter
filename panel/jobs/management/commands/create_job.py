from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from panel.jobs.models import Job
from panel.niches.models import Niche


class Command(BaseCommand):
    help = "Cria um job Create/Clip/Dub/Research para um nicho."

    def add_arguments(self, parser):
        parser.add_argument("niche_slug")
        parser.add_argument(
            "--type",
            dest="job_type",
            choices=["create", "clip", "dub", "research"],
            required=True,
        )
        parser.add_argument("--subject", default="")
        parser.add_argument("--source-url", default="")
        parser.add_argument("--cut-topic", default="")
        parser.add_argument("--enqueue", action="store_true")

    def handle(self, *args, **options):
        try:
            niche = Niche.objects.get(slug=options["niche_slug"])
        except Niche.DoesNotExist as exc:
            raise CommandError(f"Nicho não encontrado: {options['niche_slug']}") from exc

        job = Job.objects.create(
            niche=niche,
            channel=getattr(niche, "youtube_channel", None),
            job_type=options["job_type"],
            subject=options["subject"],
            source_url=options["source_url"],
            cut_topic=options["cut_topic"],
            status=Job.Status.QUEUED if options["enqueue"] else Job.Status.DRAFT,
        )
        self.stdout.write(self.style.SUCCESS(f"Job #{job.pk} ({job.public_id}) criado"))
        if options["enqueue"]:
            from panel.jobs import worker

            worker.process_job(job.pk)
            job.refresh_from_db()
            self.stdout.write(f"Status final: {job.status}")
            if job.output_video:
                self.stdout.write(f"Output: {job.output_video}")
            if job.error:
                self.stdout.write(self.style.ERROR(job.error))
