from __future__ import annotations

from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model

from panel.channels.models import YouTubeChannel
from panel.niches.models import Niche


class Command(BaseCommand):
    help = "Cria superuser local (se não existir) e um nicho de exemplo."

    def handle(self, *args, **options):
        User = get_user_model()
        if not User.objects.filter(username="admin").exists():
            User.objects.create_superuser("admin", "admin@localhost", "admin")
            self.stdout.write(self.style.SUCCESS("Superuser admin / admin criado"))
        else:
            self.stdout.write("Superuser admin já existe")

        niche, created = Niche.objects.get_or_create(
            slug="exemplo-financas",
            defaults={
                "name": "Finanças BR",
                "briefing": "Conteúdo educativo de finanças pessoais para brasileiros, tom direto e prático.",
                "keywords": "finanças pessoais\ninvestimentos brasil\nreserva de emergência",
                "default_voice": "pt-BR-FranciscaNeural-Female",
                "default_language": "pt-BR",
                "default_aspect": "9:16",
                "paragraph_number": 1,
            },
        )
        if created:
            YouTubeChannel.objects.get_or_create(niche=niche)
            self.stdout.write(self.style.SUCCESS(f"Nicho exemplo criado: {niche.slug}"))
        else:
            self.stdout.write(f"Nicho exemplo já existe: {niche.slug}")
