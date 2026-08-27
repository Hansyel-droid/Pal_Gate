import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0004_alter_user_college_department'),
    ]

    operations = [
        migrations.CreateModel(
            name='PolicyAcceptance',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('version', models.CharField(max_length=20)),
                ('accepted_at', models.DateTimeField(auto_now_add=True)),
                ('ip_address', models.GenericIPAddressField(blank=True, null=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='policy_acceptances', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-accepted_at'],
            },
        ),
        migrations.AddIndex(
            model_name='policyacceptance',
            index=models.Index(fields=['user', 'version'], name='accounts_po_user_id_64b88c_idx'),
        ),
        migrations.AddConstraint(
            model_name='policyacceptance',
            constraint=models.UniqueConstraint(fields=('user', 'version'), name='unique_policy_acceptance_per_version'),
        ),
    ]
