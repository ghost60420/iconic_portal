# Generated for the repository-pinned Django 5.2.8 on 2026-08-02

import django.db.models.deletion
from decimal import Decimal
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('contenttypes', '0002_remove_content_type_name'),
        ('crm', '0196_financial_readiness'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='FinanceOperation',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('modified_at', models.DateTimeField(auto_now=True)),
                ('submitted_at', models.DateTimeField(blank=True, null=True)),
                ('approved_at', models.DateTimeField(blank=True, null=True)),
                ('change_reason', models.TextField(blank=True, default='')),
                ('operation_number', models.CharField(max_length=40, unique=True)),
                ('operation_type', models.CharField(choices=[('CUSTOMER_PAYMENT', 'Customer payment'), ('CUSTOMER_REFUND', 'Customer refund'), ('CUSTOMER_CREDIT_NOTE', 'Customer credit note'), ('CUSTOMER_CREDIT', 'Unapplied customer credit'), ('SUPPLIER_BILL', 'Supplier bill'), ('SUPPLIER_PAYMENT', 'Supplier payment'), ('COMPANY_EXPENSE', 'Company expense'), ('UTILITY_BILL', 'Utility bill'), ('PAYROLL', 'Payroll expense'), ('PRODUCTION_COST', 'Production cost'), ('FACTORY_DAILY_COST', 'Factory daily cost'), ('BANK_DEPOSIT', 'Bank deposit'), ('BANK_WITHDRAWAL', 'Bank withdrawal'), ('CASH_DEPOSIT', 'Cash deposit'), ('CASH_WITHDRAWAL', 'Cash withdrawal'), ('ACCOUNT_TRANSFER', 'Company account transfer'), ('BANK_FEE', 'Bank fee'), ('PROCESSOR_FEE', 'Payment processor fee'), ('OWNER_INVESTMENT', 'Owner investment'), ('OWNER_WITHDRAWAL', 'Owner withdrawal'), ('LOAN_RECEIVED', 'Loan received'), ('LOAN_PRINCIPAL', 'Loan principal payment'), ('LOAN_INTEREST', 'Loan interest payment'), ('SHAREHOLDER_ADVANCE', 'Shareholder advance'), ('SHAREHOLDER_REPAYMENT', 'Shareholder repayment'), ('ASSET_PURCHASE', 'Asset purchase'), ('INVENTORY_ADJUSTMENT', 'Inventory adjustment')], db_index=True, max_length=32)),
                ('state', models.CharField(choices=[('DRAFT', 'Draft'), ('PENDING', 'Pending approval'), ('EVIDENCE_REQUIRED', 'Evidence required'), ('APPROVED', 'Approved, awaiting posting'), ('REJECTED', 'Rejected'), ('POSTED', 'Posted'), ('REVERSED', 'Reversed')], db_index=True, default='DRAFT', max_length=24)),
                ('risk_level', models.CharField(choices=[('STANDARD', 'Standard'), ('HIGH', 'High')], db_index=True, default='STANDARD', max_length=10)),
                ('transaction_date', models.DateField(db_index=True)),
                ('side', models.CharField(choices=[('CA', 'Canada'), ('BD', 'Bangladesh')], db_index=True, max_length=2)),
                ('currency', models.CharField(choices=[('CAD', 'CAD'), ('USD', 'USD'), ('BDT', 'BDT')], db_index=True, max_length=3)),
                ('amount_before_tax', models.DecimalField(decimal_places=2, default=Decimal('0'), max_digits=18)),
                ('tax_amount', models.DecimalField(decimal_places=2, default=Decimal('0'), max_digits=18)),
                ('total_amount', models.DecimalField(decimal_places=2, max_digits=18)),
                ('rate_to_cad', models.DecimalField(decimal_places=10, max_digits=20)),
                ('rate_to_bdt', models.DecimalField(decimal_places=10, max_digits=20)),
                ('amount_cad', models.DecimalField(decimal_places=2, max_digits=18)),
                ('amount_bdt', models.DecimalField(decimal_places=2, max_digits=20)),
                ('reference', models.CharField(blank=True, db_index=True, default='', max_length=120)),
                ('payment_method', models.CharField(blank=True, default='', max_length=30)),
                ('party_name', models.CharField(blank=True, default='', max_length=200)),
                ('business_purpose', models.TextField()),
                ('reason', models.TextField(blank=True, default='')),
                ('notes', models.TextField(blank=True, default='')),
                ('details', models.JSONField(blank=True, default=dict)),
                ('duplicate_warning', models.BooleanField(db_index=True, default=False)),
                ('duplicate_warning_text', models.TextField(blank=True, default='')),
                ('posting_preview', models.JSONField(blank=True, default=dict)),
                ('posting_error', models.TextField(blank=True, default='')),
                ('posting_attempted_at', models.DateTimeField(blank=True, null=True)),
                ('posted_at', models.DateTimeField(blank=True, db_index=True, null=True)),
                ('source_object_id', models.PositiveBigIntegerField(blank=True, null=True)),
                ('approved_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('customer', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='finance_operations', to='crm.customer')),
                ('department', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='finance_operations', to='crm.department')),
                ('employee', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='finance_operations', to='crm.employeeprofile')),
                ('expense_category', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='finance_operations', to='crm.expensecategory')),
                ('from_account', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='outgoing_finance_operations', to='crm.cashbankaccount')),
                ('invoice', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='finance_operations', to='crm.invoice')),
                ('modified_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('opportunity', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='finance_operations', to='crm.opportunity')),
                ('posted_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='posted_finance_operations', to=settings.AUTH_USER_MODEL)),
                ('primary_journal', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='finance_operations', to='crm.journalentry')),
                ('production_order', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='finance_operations', to='crm.productionorder')),
                ('source_content_type', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='finance_operation_sources', to='contenttypes.contenttype')),
                ('submitted_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('supplier', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='finance_operations', to='crm.supplier')),
                ('supplier_bill', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='finance_operations', to='crm.supplierbill')),
                ('to_account', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='incoming_finance_operations', to='crm.cashbankaccount')),
            ],
            options={
                'ordering': ('-transaction_date', '-id'),
                'indexes': [models.Index(fields=['operation_type', 'state'], name='fin_ops_type_state_idx'), models.Index(fields=['side', 'transaction_date'], name='fin_ops_side_date_idx'), models.Index(fields=['department', 'state'], name='fin_ops_dept_state_idx')],
                'constraints': [models.CheckConstraint(condition=models.Q(('total_amount__gt', 0)), name='finance_operation_total_positive'), models.CheckConstraint(condition=models.Q(('rate_to_cad__gt', 0)), name='finance_operation_cad_rate_positive'), models.CheckConstraint(condition=models.Q(('rate_to_bdt__gt', 0)), name='finance_operation_bdt_rate_positive')],
            },
        ),
    ]
