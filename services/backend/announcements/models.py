from django.db import models


class Announcement(models.Model):
    title = models.CharField(max_length=100, verbose_name='标题')
    content = models.TextField(blank=True, default='', verbose_name='内容')
    is_pinned = models.BooleanField(default=False, db_index=True, verbose_name='是否置顶')
    is_active = models.BooleanField(default=True, db_index=True, verbose_name='是否启用')
    sort_order = models.PositiveIntegerField(default=0, verbose_name='排序')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '公告'
        verbose_name_plural = '公告'
        ordering = ['-is_pinned', 'sort_order', '-created_at']

    def __str__(self):
        return self.title
