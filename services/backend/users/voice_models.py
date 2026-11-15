"""Private, immutable audio files owned by one store-local provider."""
import uuid
from pathlib import Path

from django.db import models


def voice_path(instance, filename):
    # Never expose user-supplied names in paths; the store storage partitions media.
    extension = Path(filename).suffix.lower()
    return f'private/audition-recordings/{uuid.uuid4().hex}{extension}'


class VoiceRecording(models.Model):
    owner = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT,
                              related_name='voice_recordings')
    audio = models.FileField(upload_to=voice_path, max_length=255)
    title = models.CharField(max_length=80)
    content_type = models.CharField(max_length=40)
    duration_ms = models.PositiveIntegerField()
    size = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    client_request_id = models.CharField(max_length=64, null=True, blank=True)
    request_fingerprint = models.CharField(max_length=64, blank=True, default='')

    class Meta:
        ordering = ['-created_at', '-id']
        indexes = [models.Index(fields=['owner', 'deleted_at', '-created_at'], name='voice_owner_list_idx')]
        constraints = [models.UniqueConstraint(fields=['owner', 'client_request_id'], name='voice_owner_request_unique')]
