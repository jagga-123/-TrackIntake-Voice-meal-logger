"""Load the Whisper checkpoint ahead of serving traffic.

Run this between ``migrate`` and the WSGI server on deploy. Loading the model
costs seconds on a laptop but can take a minute on a throttled container, and
paying that during the first user request is what makes a voice upload appear
to hang.
"""

from __future__ import annotations

import time
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from meal_voice.exceptions import TranscriptionError
from meal_voice.services.transcriber import Transcriber


class Command(BaseCommand):
    """Download (if needed) and load the configured Whisper model."""

    help = "Preload the Whisper speech-to-text model so the first request is fast."

    def add_arguments(self, parser: Any) -> None:
        """Allow a deploy to continue even if the model cannot be loaded."""
        parser.add_argument(
            "--ignore-errors",
            action="store_true",
            help="Log the failure and exit 0 instead of aborting the deploy.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Load the model, reporting how long it took."""
        from django.conf import settings

        config = settings.VOICE_MEAL
        transcriber = Transcriber(
            model_size=config["WHISPER_MODEL"],
            device=config["WHISPER_DEVICE"],
            compute_type=config["WHISPER_COMPUTE_TYPE"],
            cpu_threads=config["WHISPER_CPU_THREADS"],
        )

        started = time.monotonic()
        try:
            transcriber.model
        except TranscriptionError as exc:
            if options["ignore_errors"]:
                self.stderr.write(self.style.WARNING(f"Whisper preload failed: {exc}"))
                return
            raise CommandError(str(exc)) from exc

        elapsed = time.monotonic() - started
        self.stdout.write(
            self.style.SUCCESS(
                f"Whisper model '{config['WHISPER_MODEL']}' ready in {elapsed:.1f}s"
            )
        )
