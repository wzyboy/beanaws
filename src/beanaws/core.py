import calendar
import datetime
import importlib
import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from beancount.core import data
from beancount.parser import printer

pdftotext: Any = importlib.import_module('pdftotext')

AWS_LIABILITY_ACCOUNT = 'Liabilities:AWS'
AWS_PREPAID_ACCOUNT = 'Assets:AWS:Prepaid'

SERVICE_ACCOUNT_MAP = {
    'Amazon Simple Storage Service': 'Expenses:AWS:S3',
    'Amazon Elastic Compute Cloud': 'Expenses:AWS:EC2',
    'Amazon Relational Database Service': 'Expenses:AWS:RDS',
    'Amazon CloudFront': 'Expenses:AWS:CloudFront',
    'Amazon CloudWatch': 'Expenses:AWS:CloudWatch',
    'AmazonCloudWatch': 'Expenses:AWS:CloudWatch',
    'Elastic Load Balancing': 'Expenses:AWS:ELB',
    'Amazon Virtual Private Cloud': 'Expenses:AWS:VPC',
    'AWS Data Transfer': 'Expenses:AWS:DataTransfer',
    'Amazon Route 53': 'Expenses:AWS:Route53',
    'AWS Lambda': 'Expenses:AWS:Lambda',
    'Amazon Simple Notification Service': 'Expenses:AWS:SNS',
    'AWS Glue': 'Expenses:AWS:Glue',
    'AWS Key Management Service': 'Expenses:AWS:KMS',
}

TAX_ACCOUNT_MAP = {
    'GST': 'Expenses:AWS:Tax:GST',
    'BC PST': 'Expenses:AWS:Tax:BCPST',
    'HST': 'Expenses:AWS:Tax:HST',
    'QST': 'Expenses:AWS:Tax:QST',
}
ROUNDING_ACCOUNT = 'Expenses:AWS:Rounding'

SERVICE_LINE_RE = re.compile(r'^(?P<label>.+?)\s+(?P<amount>-?(?:USD|CAD)\s*[\d,]+\.\d\d)$')
AMOUNT_RE = re.compile(r'(-?(?:USD|CAD)\s*[\d,]+\.\d\d)')


@dataclass(frozen=True)
class ReservedTerm:
    start_date: datetime.date
    months: int


@dataclass(frozen=True)
class ServiceCharge:
    label: str
    account: str
    total: Decimal
    pretax: Decimal
    taxes: dict[str, Decimal]
    reserved_term: ReservedTerm | None = None


@dataclass(frozen=True)
class AWSDocument:
    number: str
    document_date: datetime.date
    period_start: datetime.date
    period_end: datetime.date
    currency: str
    total: Decimal
    pretax_total: Decimal
    total_tax: Decimal
    taxes: dict[str, Decimal]
    service_charges: list[ServiceCharge]


class AWSDocumentParser:
    def get_full_text(self, pdf_path: str | Path) -> str:
        with open(pdf_path, 'rb') as f:
            pdf = pdftotext.PDF(f, physical=True)
        return unicodedata.normalize('NFKC', '\n\n'.join(pdf))

    def identify(self, filepath: str) -> bool:
        if not filepath.lower().endswith('.pdf'):
            return False
        text = self.get_full_text(filepath)
        return 'TOTAL AMOUNT / MONTANT TOTAL' in text and (
            'AWS Service Charges' in text or 'console.aws.amazon.com' in text
        )

    def parse_document(self, filepath: str | Path) -> AWSDocument:
        text = self.get_full_text(filepath)
        return parse_aws_document(text)


def parse_aws_document(text: str) -> AWSDocument:
    normalized = unicodedata.normalize('NFKC', text).replace('\uf0b7', ' ')
    if re.search(r'^\s*Credit Note /', normalized, re.MULTILINE):
        raise ValueError('AWS credit notes are no longer supported')
    number = require_match(r'Invoice Number.*?([A-Z]{4,5}\d{2}-\d+)', normalized)
    document_date = parse_slash_date(require_match(r'Tax Invoice Date.*?(\d{4}/\d{2}/\d{2})', normalized))
    period_match = re.search(
        r'billing period .*?(\d{4}/\d{2}/\d{2}).*?(\d{4}/\d{2}/\d{2})',
        normalized,
        re.IGNORECASE | re.DOTALL,
    )
    if period_match is None:
        raise ValueError('Unable to find AWS billing period')
    period_start = parse_slash_date(period_match.group(1))
    period_end = parse_slash_date(period_match.group(2))

    summary_text = normalized.split('Detail / Détail', 1)[0]
    currency = 'USD'
    total = require_summary_amount(summary_text, 'AWS Service Charges', currency)
    total_tax = require_summary_amount(summary_text, 'Total taxes / Montant total des taxes', currency)
    pretax_total = parse_summary_pretax_total(summary_text, currency, total, total_tax)
    taxes = parse_summary_taxes(summary_text, currency)
    service_charges = parse_detail_service_charges(normalized, currency)
    if len(service_charges) == 1 and service_charges[0].reserved_term is not None:
        service_charge = service_charges[0]
        service_charges = [
            ServiceCharge(
                label=service_charge.label,
                account=service_charge.account,
                total=service_charge.total,
                pretax=pretax_total,
                taxes=service_charge.taxes,
                reserved_term=service_charge.reserved_term,
            )
        ]
    return AWSDocument(
        number=number,
        document_date=document_date,
        period_start=period_start,
        period_end=period_end,
        currency=currency,
        total=total,
        pretax_total=pretax_total,
        total_tax=total_tax,
        taxes=taxes,
        service_charges=service_charges,
    )


def parse_summary_taxes(text: str, currency: str) -> dict[str, Decimal]:
    taxes: dict[str, Decimal] = {}
    for label in TAX_ACCOUNT_MAP:
        tax_amount = find_summary_amount(text, f'Total {label} Amount', currency)
        if tax_amount is not None:
            taxes[label] = tax_amount
    return taxes


def parse_detail_service_charges(text: str, currency: str) -> list[ServiceCharge]:
    detail_match = re.search(r'Detail / Détail\s*(.*)', text, re.DOTALL)
    if detail_match is None:
        raise ValueError('Unable to find AWS detail section')
    detail_text = detail_match.group(1)
    lines: list[str] = []
    for raw_line in detail_text.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue
        if line.startswith('Amazon Web Services Canada, Inc.'):
            continue
        if re.match(r'^\s*\d+/\d+\s*$', line):
            continue
        lines.append(line)

    service_charges: list[ServiceCharge] = []
    current_block: list[str] = []
    for line in lines:
        if line.startswith(' ') or line.startswith('\t'):
            if current_block:
                current_block.append(line)
            continue
        if current_block:
            service_charge = parse_service_block(current_block, currency)
            if service_charge is not None:
                service_charges.append(service_charge)
        current_block = [line]

    if current_block:
        service_charge = parse_service_block(current_block, currency)
        if service_charge is not None:
            service_charges.append(service_charge)
    return service_charges


def parse_service_block(lines: list[str], currency: str) -> ServiceCharge | None:
    top_match = SERVICE_LINE_RE.match(lines[0].strip())
    if top_match is None:
        return None
    amount_currency, total = parse_currency_amount(top_match.group('amount'))
    if amount_currency != currency:
        return None
    if total == Decimal('0.00'):
        return None

    label = split_english_label(top_match.group('label'))
    taxes: dict[str, Decimal] = {}
    explicit_pretax: Decimal | None = None
    start_date: datetime.date | None = None
    duration_months: int | None = None

    for raw_line in lines[1:]:
        line = raw_line.strip()
        if not line:
            continue
        amount_match = AMOUNT_RE.search(line)
        if amount_match is not None:
            amount_currency, line_amount = parse_currency_amount(amount_match.group(1))
            if amount_currency != currency:
                continue
            if 'Charges / Frais' in line:
                explicit_pretax = line_amount
            elif 'one time fee' in line:
                explicit_pretax = line_amount
            elif 'GST / TPS' in line:
                taxes['GST'] = line_amount
            elif 'BC PST / BC TVP' in line:
                taxes['BC PST'] = line_amount
            elif 'HST' in line:
                taxes['HST'] = line_amount
            elif 'QST' in line:
                taxes['QST'] = line_amount

        if start_date is None:
            start_match = re.search(r'Start Date - (\d{4}/\d{2}/\d{2}|\d{2}/\d{2}/\d{4})', line)
            if start_match is not None:
                start_date = parse_flexible_date(start_match.group(1))
        if duration_months is None:
            duration_match = re.search(r'Duration - (\d+)\s*yr', line)
            if duration_match is not None:
                duration_months = int(duration_match.group(1)) * 12

    pretax = explicit_pretax if explicit_pretax is not None else total - sum(taxes.values(), Decimal('0.00'))
    reserved_term = None
    if start_date is not None and duration_months is not None:
        reserved_term = ReservedTerm(start_date=start_date, months=duration_months)

    return ServiceCharge(
        label=label,
        account=service_label_to_account(label),
        total=total,
        pretax=pretax,
        taxes=taxes,
        reserved_term=reserved_term,
    )


def split_english_label(label: str) -> str:
    english = label.split(' / ', 1)[0].strip()
    return ' '.join(english.split())


def service_label_to_account(label: str) -> str:
    mapped = SERVICE_ACCOUNT_MAP.get(label)
    if mapped is not None:
        return mapped
    cleaned = re.sub(r'^(Amazon|AWS)\s+', '', label).strip()
    parts = re.findall(r'[A-Za-z0-9]+', cleaned)
    if not parts:
        return 'Expenses:AWS:Other'
    return f'Expenses:AWS:{"".join(part.capitalize() for part in parts)}'


def build_document_entries(document: AWSDocument) -> data.Entries:
    entries: data.Entries = []
    entries.append(build_document_transaction(document))
    for service_charge in document.service_charges:
        if service_charge.reserved_term is None:
            continue
        entries.extend(build_amortization_entries(document, service_charge))
    return entries


def build_document_transaction(document: AWSDocument) -> data.Transaction:
    txn = data.Transaction(
        meta=build_metadata(document),
        date=document.document_date,
        flag='*',
        payee='AWS',
        narration='AWS Invoice',
        tags=data.EMPTY_SET,
        links=data.EMPTY_SET,
        postings=[],
    )
    currency = document.currency

    has_reserved = any(service_charge.reserved_term is not None for service_charge in document.service_charges)
    if has_reserved:
        data.create_simple_posting(txn, AWS_PREPAID_ACCOUNT, document.pretax_total, currency)
    else:
        for service_charge in document.service_charges:
            data.create_simple_posting(txn, service_charge.account, service_charge.pretax, currency)

    tax_totals = collect_tax_totals(document)
    for label, tax_amount in sorted(tax_totals.items()):
        tax_account = TAX_ACCOUNT_MAP[label]
        data.create_simple_posting(txn, tax_account, tax_amount, currency)

    imbalance = document.total - sum(posting.units.number for posting in txn.postings if posting.units is not None)
    if imbalance != Decimal('0.00'):
        data.create_simple_posting(txn, ROUNDING_ACCOUNT, imbalance, currency)

    data.create_simple_posting(txn, AWS_LIABILITY_ACCOUNT, -document.total, currency)
    return txn


def build_amortization_entries(document: AWSDocument, service_charge: ServiceCharge) -> data.Entries:
    assert service_charge.reserved_term is not None
    term = service_charge.reserved_term
    monthly_amounts = split_monthly_amounts(service_charge.pretax, term.months)
    entries: data.Entries = []
    for idx, monthly_amount in enumerate(monthly_amounts):
        amortization_date = add_months(term.start_date, idx)
        txn = data.Transaction(
            meta=build_metadata(document, service_charge=service_charge, amortization_index=idx + 1),
            date=amortization_date,
            flag='*',
            payee='AWS',
            narration=f'AWS Amortization {document.number}',
            tags=data.EMPTY_SET,
            links=data.EMPTY_SET,
            postings=[],
        )
        data.create_simple_posting(txn, service_charge.account, monthly_amount, document.currency)
        data.create_simple_posting(txn, AWS_PREPAID_ACCOUNT, -monthly_amount, document.currency)
        entries.append(txn)
    return entries


def build_metadata(
    document: AWSDocument,
    *,
    service_charge: ServiceCharge | None = None,
    amortization_index: int | None = None,
) -> dict[str, Any]:
    meta = data.new_metadata('aws', 0)
    meta['document'] = document.number
    meta['billing_period_start'] = document.period_start.isoformat()
    meta['billing_period_end'] = document.period_end.isoformat()
    if service_charge is not None:
        meta['service'] = service_charge.label
    if service_charge is not None and service_charge.reserved_term is not None:
        meta['ri_start_date'] = service_charge.reserved_term.start_date.isoformat()
        meta['ri_months'] = str(service_charge.reserved_term.months)
    if amortization_index is not None:
        meta['amortization_month'] = str(amortization_index)
    return meta


def collect_tax_totals(document: AWSDocument) -> dict[str, Decimal]:
    if document.taxes:
        return document.taxes
    tax_totals: dict[str, Decimal] = {}
    for service_charge in document.service_charges:
        for label, tax_amount in service_charge.taxes.items():
            tax_totals[label] = tax_totals.get(label, Decimal('0.00')) + tax_amount
    return tax_totals


def split_monthly_amounts(total: Decimal, months: int) -> list[Decimal]:
    if months <= 0:
        raise ValueError('Reserved term months must be positive')
    quantized_total = total.quantize(Decimal('0.01'))
    if months == 1:
        return [quantized_total]
    base = (quantized_total / Decimal(months)).quantize(Decimal('0.01'))
    amounts = [base for _ in range(months)]
    amounts[-1] = quantized_total - sum(amounts[:-1], Decimal('0.00'))
    return amounts


def add_months(date: datetime.date, months: int) -> datetime.date:
    year = date.year + (date.month - 1 + months) // 12
    month = (date.month - 1 + months) % 12 + 1
    day = min(date.day, calendar.monthrange(year, month)[1])
    return datetime.date(year, month, day)


def parse_slash_date(value: str) -> datetime.date:
    return datetime.date.fromisoformat(value.replace('/', '-'))


def parse_flexible_date(value: str) -> datetime.date:
    if re.match(r'\d{4}/\d{2}/\d{2}$', value):
        return parse_slash_date(value)
    if re.match(r'\d{2}/\d{2}/\d{4}$', value):
        month, day, year = value.split('/')
        return datetime.date(int(year), int(month), int(day))
    raise ValueError(f'Unsupported date format: {value}')


def parse_currency_amount(value: str) -> tuple[str, Decimal]:
    normalized = value.replace(' ', '')
    sign = -1 if normalized.startswith('-') else 1
    if sign == -1:
        normalized = normalized[1:]
    currency = normalized[:3]
    number = Decimal(normalized[3:].replace(',', '')) * sign
    return currency, number


def find_summary_amount(text: str, label: str, currency: str) -> Decimal | None:
    for line in text.splitlines():
        if not line.strip().startswith(label):
            continue
        for amount_text in AMOUNT_RE.findall(line):
            parsed_currency, parsed_amount = parse_currency_amount(amount_text)
            if parsed_currency == currency:
                return parsed_amount
    return None


def require_summary_amount(text: str, label: str, currency: str) -> Decimal:
    value = find_summary_amount(text, label, currency)
    if value is None:
        raise ValueError(f'Unable to find summary amount for {label!r} in {currency}')
    return value


def parse_summary_pretax_total(text: str, currency: str, total: Decimal, total_tax: Decimal) -> Decimal:
    charges_amount = find_summary_amount(text, 'Charges / Frais', currency)
    if charges_amount is not None:
        return charges_amount
    for line in text.splitlines():
        stripped = line.strip()
        if 'one time fee' not in stripped:
            continue
        for amount_text in AMOUNT_RE.findall(stripped):
            parsed_currency, parsed_amount = parse_currency_amount(amount_text)
            if parsed_currency == currency:
                return parsed_amount
    return total - total_tax


def require_match(pattern: str, text: str) -> str:
    match = re.search(pattern, text, re.DOTALL)
    if match is None:
        raise ValueError(f'Unable to match pattern {pattern!r}')
    return match.group(1)


def gather_public_entries(pdf_paths: list[Path]) -> data.Entries:
    importer = AWSDocumentParser()
    entries: data.Entries = []
    documents = [importer.parse_document(path) for path in pdf_paths]
    documents.sort(key=lambda doc: (doc.document_date, doc.number))
    for document in documents:
        entries.extend(build_document_entries(document))
    return entries


def gather_accounts(entries: data.Entries) -> list[str]:
    accounts = {
        posting.account for entry in entries if isinstance(entry, data.Transaction) for posting in entry.postings
    }
    return sorted(accounts)


def render_public_bean(entries: data.Entries, *, title: str = 'AWS Costs') -> str:
    open_date = min(entry.date for entry in entries) if entries else datetime.date(2000, 1, 1)
    lines = [f'option "title" "{title}"', 'option "operating_currency" "USD"', '']
    for account_name in gather_accounts(entries):
        open_entry = data.Open(
            meta=data.new_metadata('aws', 0),
            date=open_date,
            account=account_name,
            currencies=[],
            booking=None,
        )
        lines.append(printer.format_entry(open_entry).rstrip())
        lines.append('')
    for entry in sorted(entries, key=lambda item: (item.date, sort_key_for_entry(item))):
        lines.append(printer.format_entry(entry).rstrip())
        lines.append('')
    return '\n'.join(lines).rstrip() + '\n'


def sort_key_for_entry(entry: data.Directive) -> tuple[str, str]:
    if not isinstance(entry, data.Transaction):
        return ('', '')
    document = str(entry.meta.get('document', ''))
    month = str(entry.meta.get('amortization_month', '0'))
    return (document, month)


def generate_public_bean_text(pdf_paths: list[Path], *, title: str = 'AWS Costs') -> str:
    entries = gather_public_entries(pdf_paths)
    return render_public_bean(entries, title=title)


def rename_pdfs_with_dates(pdf_paths: list[Path]) -> list[tuple[Path, Path]]:
    importer = AWSDocumentParser()
    renamed: list[tuple[Path, Path]] = []
    for pdf_path in sorted(pdf_paths):
        dest_path = build_dated_pdf_path(pdf_path, importer.parse_document(pdf_path).document_date)
        if dest_path == pdf_path:
            continue
        if dest_path.exists():
            raise FileExistsError(f'Destination {dest_path} already exists')
        pdf_path.rename(dest_path)
        renamed.append((pdf_path, dest_path))
    return renamed


def build_dated_pdf_path(pdf_path: Path, document_date: datetime.date) -> Path:
    if re.search(r'\d{4}-\d{2}', pdf_path.stem):
        return pdf_path
    return pdf_path.with_name(f'{document_date.isoformat()}.{pdf_path.name}')
