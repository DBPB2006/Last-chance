from decimal import Decimal
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def create_existing_user_wallets(apps, schema_editor):
    User = apps.get_model('accounts', 'User')
    Wallet = apps.get_model('accounts', 'Wallet')
    for user in User.objects.all():
        Wallet.objects.get_or_create(user=user)


def reverse_wallets(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('accounts', '0002_alter_setukycrecord_options'),
        ('orders', '0002_alter_order_order_number'),
        ('ticketing', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='Wallet',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('balance', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=12)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='wallet', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='WalletTransaction',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('amount', models.DecimalField(decimal_places=2, max_digits=10)),
                ('transaction_type', models.CharField(choices=[('CREDIT', 'Resale Credit')], default='CREDIT', max_length=20)),
                ('description', models.CharField(blank=True, default='', max_length=255)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('order', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='wallet_transaction', to='orders.order')),
                ('ticket', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='wallet_credits', to='ticketing.ticket')),
                ('wallet', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='transactions', to='accounts.wallet')),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.RunPython(create_existing_user_wallets, reverse_wallets),
    ]
