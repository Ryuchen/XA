from django.core.validators import RegexValidator
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = []
    operations = [
        migrations.CreateModel(
            name='EntityNumber',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('code', models.CharField(max_length=14, unique=True, validators=[RegexValidator(r'^[A-Z0-9]{2,4}[A-Z0-9]{10}$', '实体编号格式不正确')])),
                ('entity_type', models.CharField(db_index=True, max_length=100)),
                ('entity_id', models.CharField(max_length=64)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'verbose_name': '实体编号',
                'verbose_name_plural': '实体编号',
                'indexes': [models.Index(fields=['entity_type', 'entity_id'], name='entity_number_lookup_idx')],
                'constraints': [models.UniqueConstraint(fields=('entity_type', 'entity_id'), name='uniq_entity_number_type_id')],
            },
        ),
    ]
