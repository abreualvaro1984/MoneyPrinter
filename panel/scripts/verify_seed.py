import os
import sys
from pathlib import Path

import django

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "panel.config.settings")
django.setup()

from panel.channels.models import YouTubeChannel
from panel.jobs.models import Job
from panel.niches.models import Niche

n = Niche.objects.get(slug="exemplo-financas")
print("niche", n.name, n.default_voice)
print("channel", YouTubeChannel.objects.filter(niche=n).first())
print("jobs", Job.objects.count())
