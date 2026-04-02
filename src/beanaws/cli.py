from pathlib import Path

import click

from beanaws.core import generate_public_bean_text
from beanaws.core import rename_pdfs_with_dates


def collect_pdf_paths(inputs: tuple[Path, ...]) -> list[Path]:
    pdf_paths: list[Path] = []
    for input_path in inputs:
        if input_path.is_dir():
            pdf_paths.extend(sorted(path for path in input_path.iterdir() if path.is_file() and path.suffix.lower() == '.pdf'))
            continue
        if input_path.is_file() and input_path.suffix.lower() == '.pdf':
            pdf_paths.append(input_path)
    return sorted(set(pdf_paths))


@click.group()
def main() -> None:
    pass


@main.command()
@click.argument('inputs', nargs=-1, type=click.Path(exists=True, path_type=Path))
@click.option('-o', '--output', type=click.Path(path_type=Path), default=None)
def generate(inputs: tuple[Path, ...], output: Path | None) -> None:
    """Generate Beancount files from AWS invoice PDF files"""
    pdf_paths = collect_pdf_paths(inputs)
    bean_text = generate_public_bean_text(pdf_paths)
    if output is None:
        click.echo(bean_text, nl=False)
        return
    output.write_text(bean_text)


@main.command()
@click.argument('inputs', nargs=-1, type=click.Path(exists=True, path_type=Path))
def rename(inputs: tuple[Path, ...]) -> None:
    """Rename AWS invoice PDF files with dates in filenames"""
    pdf_paths = collect_pdf_paths(inputs)
    for src_path, dest_path in rename_pdfs_with_dates(pdf_paths):
        click.echo(f'{src_path} -> {dest_path}')


if __name__ == '__main__':
    main()
