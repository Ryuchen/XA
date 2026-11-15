from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('audition', '0005_signup_notice_cycle'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name='auditionsignup',
            name='applicant',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE,
                                    related_name='audition_signups', to=settings.AUTH_USER_MODEL,
                                    verbose_name='报名人'),
        ),
        migrations.AddConstraint(
            model_name='auditionsignup',
            constraint=models.UniqueConstraint(fields=('link', 'applicant_account'),
                                               name='audition_link_account_unique'),
        ),
    ]
