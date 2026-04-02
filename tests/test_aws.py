import datetime
from decimal import Decimal
from pathlib import Path
from typing import cast

from beancount.core import amount
from beancount.core import data

from beanaws.core import AWS_LIABILITY_ACCOUNT
from beanaws.core import AWS_PREPAID_ACCOUNT
from beanaws.core import build_dated_pdf_path
from beanaws.core import build_document_entries
from beanaws.core import generate_public_bean_text
from beanaws.core import parse_aws_document
from beanaws.core import parse_pdf_document
from beanaws.core import rename_pdfs_with_dates
from beanaws.core import split_monthly_amounts

MONTHLY_INVOICE_TEXT = """
Invoice / Facture
Account number / Numéro de compte : 123456789012
Invoice Summary / Résumé de la facture
Invoice Number: / Numéro de facture : CAIN26-659888
Tax Invoice Date: / Date de la facture fiscale : 2026/03/01
Address / Adresse :
Example Corp
ATTN: / À l’attention de : Example Person
123 Example Street
Example City, British Columbia, A1A 1A1, CA
TOTAL AMOUNT / MONTANT TOTAL USD 119.69
This Invoice is for the billing period 2026/02/01 - 2026/02/28
Invoice Summary / Résumé de la facture
AWS Service Charges / Frais de service AWS USD 119.69
Charges / Frais USD 106.87
Total taxes / Montant total des taxes USD 12.82
Total BC PST Amount at 7% / Montant total de la BC TVP (7 %) USD 7.48
Total GST Amount at 5% / Montant total de la TPS (5 %) USD 5.34
Detail / Détail
AmazonCloudWatch / AmazonCloudWatch USD 0.00
Elastic Load Balancing / Elastic Load Balancing USD 30.88
  Charges / Frais USD 27.57
  BC PST / BC TVP USD 1.93
  GST / TPS USD 1.38
Amazon Simple Storage Service / Amazon Simple Storage Service USD 5.35
  Charges / Frais USD 4.77
  BC PST / BC TVP USD 0.34
  GST / TPS USD 0.24
Amazon Elastic Compute Cloud / Amazon Elastic Compute Cloud USD 11.39
  Charges / Frais USD 10.17
  BC PST / BC TVP USD 0.71
  GST / TPS USD 0.51
Amazon Virtual Private Cloud / Amazon Virtual Private Cloud USD 18.91
  Charges / Frais USD 16.89
  BC PST / BC TVP USD 1.18
  GST / TPS USD 0.84
AWS Data Transfer / AWS Data Transfer USD 31.16
  Charges / Frais USD 27.82
  BC PST / BC TVP USD 1.95
  GST / TPS USD 1.39
Amazon Relational Database Service / Amazon Relational Database Service USD 21.31
  Charges / Frais USD 19.03
  BC PST / BC TVP USD 1.33
  GST / TPS USD 0.95
Amazon CloudFront / Amazon CloudFront USD 0.00
AWS Glue / AWS Glue USD 0.00
Amazon Route 53 / Amazon Route 53 USD 0.69
  Charges / Frais USD 0.62
  BC PST / BC TVP USD 0.04
  GST / TPS USD 0.03
AWS Key Management Service / AWS Key Management Service USD 0.00
""".strip()

RI_INVOICE_TEXT = """
Invoice / Facture
Account number / Numéro de compte : 123456789012
Invoice Summary / Résumé de la facture
Invoice Number: / Numéro de facture : CAIN25-2977137
Tax Invoice Date: / Date de la facture : 2025/12/18
ATTN: / À l’attention de : Example Person
TOTAL AMOUNT / MONTANT TOTAL USD 1,880.48
This Invoice is for the billing period 2025/12/01 - 2025/12/31
Invoice Summary / Résumé de la facture
AWS Service Charges / Frais de service AWS USD 1,880.48
1 x Amazon Relational Database Service (one time fee) / 1 x Amazon Relational Database Service (frais uniques) USD 1,679.00
Total taxes / Montant total des taxes USD 201.48
Total BC PST Amount at 7% / Montant total de la BC TVP (7 %) USD 117.53
Total GST Amount at 5% / Montant total de la TPS (5 %) USD 83.95
Detail / Détail
Amazon Relational Database Service / Amazon Relational Database Service USD 1,880.48
  1 x Amazon Relational Database Service (one time fee) / 1 x Amazon Relational Database Service (frais uniques) USD 1,679.00
  Start Date - 2025/12/18 / Date de début : 2025/12/18; Duration - 3yr / Durée : 3 ans
  db.m6g.large
  GST / TPS USD 83.95
  BC PST / BC TVP USD 117.53
""".strip()

CAD_MONTHLY_INVOICE_TEXT = """
Invoice / Facture
Invoice Number / Numéro de facture: CAIN23-51108
Tax Invoice Date / Date de facture fiscale: 2023/01/02
TOTAL AMOUNT / MONTANT TOTAL CAD 31.00
This Invoice is for the billing period 2022/12/01 - 2022/12/31
Invoice Summary / Résumé facture
AWS Service Charges / Frais de service AWS (1 USD = 1.3729618618 CAD) USD 22.58 CAD 31.00
Charges / Frais USD 20.15 CAD 27.67
Total taxes / Montant total des taxes USD 2.43 CAD 3.34
Total GST Amount at 5% / Montant total TPS à 5% USD 1.02 CAD 1.40
Total BC PST Amount at 7% / Montant total BC TVP à 7% USD 1.41 CAD 1.94
Detail / Détail
Amazon Simple Storage Service USD 0.36
  Charges / Frais USD 0.32
  BC PST / BC TVP USD 0.02
  GST / TPS USD 0.02
AWS Data Transfer USD 8.10
  Charges / Frais USD 7.23
  BC PST / BC TVP USD 0.51
  GST / TPS USD 0.36
Elastic Load Balancing USD 0.12
  Charges / Frais USD 0.10
  BC PST / BC TVP USD 0.01
  GST / TPS USD 0.01
Amazon Relational Database Service USD 12.54
  Charges / Frais USD 11.20
  GST / TPS USD 0.56
  BC PST / BC TVP USD 0.78
Amazon Elastic Compute Cloud USD 0.81
  Charges / Frais USD 0.72
  BC PST / BC TVP USD 0.05
  GST / TPS USD 0.04
Amazon Route 53 USD 0.65
  Charges / Frais USD 0.58
  GST / TPS USD 0.03
  BC PST / BC TVP USD 0.04
""".strip()

def test_parse_monthly_invoice_extracts_services_and_taxes() -> None:
    document = parse_aws_document(MONTHLY_INVOICE_TEXT)

    assert document.number == 'CAIN26-659888'
    assert document.document_date == datetime.date(2026, 3, 1)
    assert document.period_start == datetime.date(2026, 2, 1)
    assert document.period_end == datetime.date(2026, 2, 28)
    assert document.currency == 'USD'
    assert document.total == Decimal('119.69')
    assert document.taxes == {
        'BC PST': Decimal('7.48'),
        'GST': Decimal('5.34'),
    }
    assert [charge.account for charge in document.service_charges] == [
        'Expenses:AWS:ELB',
        'Expenses:AWS:S3',
        'Expenses:AWS:EC2',
        'Expenses:AWS:VPC',
        'Expenses:AWS:DataTransfer',
        'Expenses:AWS:RDS',
        'Expenses:AWS:Route53',
    ]
    assert all(charge.total != Decimal('0.00') for charge in document.service_charges)


def test_build_document_entries_for_monthly_invoice_splits_service_and_tax_postings() -> None:
    document = parse_aws_document(MONTHLY_INVOICE_TEXT)

    entries = build_document_entries(document)

    assert len(entries) == 1
    txn = cast(data.Transaction, entries[0])
    assert txn.meta['document'] == 'CAIN26-659888'
    assert [posting.account for posting in txn.postings] == [
        'Expenses:AWS:ELB',
        'Expenses:AWS:S3',
        'Expenses:AWS:EC2',
        'Expenses:AWS:VPC',
        'Expenses:AWS:DataTransfer',
        'Expenses:AWS:RDS',
        'Expenses:AWS:Route53',
        'Expenses:AWS:Tax:BCPST',
        'Expenses:AWS:Tax:GST',
        AWS_LIABILITY_ACCOUNT,
    ]
    assert txn.postings[-1].units == amount.from_string('-119.69 USD')


def test_build_document_entries_for_ri_invoice_uses_prepaid_and_amortization() -> None:
    document = parse_aws_document(RI_INVOICE_TEXT)

    entries = build_document_entries(document)

    assert len(entries) == 37
    invoice_txn = cast(data.Transaction, entries[0])
    first_amortization = cast(data.Transaction, entries[1])
    last_amortization = cast(data.Transaction, entries[-1])
    assert [posting.account for posting in invoice_txn.postings] == [
        AWS_PREPAID_ACCOUNT,
        'Expenses:AWS:Tax:BCPST',
        'Expenses:AWS:Tax:GST',
        AWS_LIABILITY_ACCOUNT,
    ]
    assert invoice_txn.postings[0].units == amount.from_string('1679.00 USD')
    assert first_amortization.date == datetime.date(2025, 12, 18)
    assert first_amortization.postings[0].account == 'Expenses:AWS:RDS'
    assert first_amortization.postings[1].account == AWS_PREPAID_ACCOUNT
    assert first_amortization.meta['ri_months'] == '36'
    assert last_amortization.date == datetime.date(2028, 11, 18)
    monthly_postings = [cast(data.Transaction, entry).postings[0] for entry in entries[1:]]
    assert sum(posting.units.number for posting in monthly_postings if posting.units is not None) == Decimal(
        '1679.00'
    )


def test_build_document_entries_for_cad_invoice_convert_detail_amounts() -> None:
    document = parse_aws_document(CAD_MONTHLY_INVOICE_TEXT)

    entries = build_document_entries(document)

    txn = cast(data.Transaction, entries[0])
    assert document.currency == 'USD'
    assert document.total == Decimal('22.58')
    assert document.pretax_total == Decimal('20.15')
    assert document.total_tax == Decimal('2.43')
    assert txn.postings[-1].units == amount.from_string('-22.58 USD')
    assert sum(posting.units.number for posting in txn.postings if posting.units is not None) == Decimal('0.00')


def test_split_monthly_amounts_keeps_rounding_remainder_in_last_month() -> None:
    amounts = split_monthly_amounts(Decimal('100.00'), 3)

    assert amounts == [Decimal('33.33'), Decimal('33.33'), Decimal('33.34')]


def test_generate_public_bean_text_is_sanitized(monkeypatch, tmp_path: Path) -> None:
    invoice_path = tmp_path / 'invoice.pdf'
    ri_path = tmp_path / 'ri.pdf'
    invoice_path.write_text('x')
    ri_path.write_text('x')
    documents = {
        invoice_path: parse_aws_document(MONTHLY_INVOICE_TEXT),
        ri_path: parse_aws_document(RI_INVOICE_TEXT),
    }
    monkeypatch.setattr('beanaws.core.parse_pdf_document', lambda path: documents[Path(path)])

    bean_text = generate_public_bean_text([invoice_path, ri_path])

    assert 'option "title" "AWS Costs"' in bean_text
    assert 'open Liabilities:AWS' in bean_text
    assert 'open Assets:AWS:Prepaid' in bean_text
    assert 'Expenses:AWS:RDS' in bean_text
    assert 'Example Person' not in bean_text
    assert 'Example Street' not in bean_text
    assert '123456789012' not in bean_text
    assert str(tmp_path) not in bean_text


def test_build_dated_pdf_path_prefixes_missing_date() -> None:
    pdf_path = Path('/tmp/CAIN26-659888.pdf')

    dated_path = build_dated_pdf_path(pdf_path, datetime.date(2026, 3, 1))

    assert dated_path == Path('/tmp/2026-03-01.CAIN26-659888.pdf')


def test_build_dated_pdf_path_preserves_existing_iso_date() -> None:
    pdf_path = Path('/tmp/2026-03-01.CAIN26-659888.pdf')

    dated_path = build_dated_pdf_path(pdf_path, datetime.date(2026, 3, 1))

    assert dated_path == pdf_path


def test_rename_pdfs_with_dates_renames_in_place(monkeypatch, tmp_path: Path) -> None:
    pdf_path = tmp_path / 'CAIN26-659888.pdf'
    pdf_path.write_text('x')
    document = parse_aws_document(MONTHLY_INVOICE_TEXT)
    monkeypatch.setattr('beanaws.core.parse_pdf_document', lambda path: document)

    renamed = rename_pdfs_with_dates([pdf_path])

    assert renamed == [(pdf_path, tmp_path / '2026-03-01.CAIN26-659888.pdf')]
    assert not pdf_path.exists()
    assert (tmp_path / '2026-03-01.CAIN26-659888.pdf').exists()
