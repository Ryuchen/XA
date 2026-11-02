from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Trim


def tags_ready_for_review(queryset):
    # 与工作台计数共用条件；空内容和等待用户修改的标签不进入待审队列。
    return (queryset.filter(kind_snapshot='TAG', status__in=['PENDING', 'FULFILLING'],
                            tag_revision_required=False)
            .annotate(tag_candidate=Trim(KeyTextTransform('tag', 'details')))
            .filter(tag_candidate__isnull=False).exclude(tag_candidate=''))
