from __future__ import annotations

from django.core.management.base import BaseCommand

from panel.jobs import worker


class Command(BaseCommand):
    help = "Processa jobs com status=queued (Create / Clip / Dub / Research)."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=5)
        parser.add_argument(
            "--loop",
            action="store_true",
            help="Fica em loop polling a cada --interval segundos.",
        )
        parser.add_argument("--interval", type=int, default=10)

    def handle(self, *args, **options):
        import time

        limit = options["limit"]
        if not options["loop"]:
            n = worker.process_queued(limit=limit)
            self.stdout.write(self.style.SUCCESS(f"Processados: {n}"))
            return

        self.stdout.write("Worker em loop. Ctrl+C para sair.")
        while True:
            n = worker.process_queued(limit=limit)
            if n:
                self.stdout.write(self.style.SUCCESS(f"Processados: {n}"))
            time.sleep(options["interval"])
