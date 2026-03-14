import argparse
import sys
import csv
import re
import configparser
from pathlib import Path

try:
    from tqdm import tqdm
except ImportError:
    def tqdm(iterable, *args, **kwargs):
        total = kwargs.get('total', len(iterable) if hasattr(iterable, '__len__') else None)
        desc = kwargs.get('desc', 'Processing')
        for i, item in enumerate(iterable, 1):
            if total:
                sys.stdout.write(f"\r[INFO] {desc}: {i}/{total} ({(i / total) * 100:.1f}%)")
            else:
                sys.stdout.write(f"\r[INFO] {desc}: {i} items")
            sys.stdout.flush()
            yield item
        print()

from processors.identifier import LanguageIdentifier
from processors.translator import LindatTranslator
from utils import process_alto_xml, process_amcr_xml
import requests
import tempfile


def fetch_xml_from_url(url, download_dir):
    """Downloads an XML file from a URL to a local directory."""
    try:
        response = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'})
        response.raise_for_status()

        # Extract a meaningful filename from the URL identifier
        doc_id = url.split('=')[-1].replace('https://api.aiscr.cz/id/', '')
        safe_name = "".join([c for c in doc_id if c.isalpha() or c.isdigit() or c in ('-', '_')]).rstrip()
        local_path = download_dir / f"{safe_name}.xml"

        with open(local_path, 'wb') as f:
            f.write(response.content)
        return local_path
    except Exception as e:
        print(f"[ERROR] Failed to download {url}: {e}")
        return None

def parse_arguments():
    parser = argparse.ArgumentParser(description="ATRIUM - Lindat Translation Wrapper (XML Focused)")
    parser.add_argument("input_path", type=Path, nargs='?', default=None)
    parser.add_argument("--output", "-o", type=Path, default=None)
    parser.add_argument("--source_lang", "-src", type=str, default="cs")
    parser.add_argument("--target_lang", "-tgt", type=str, default="en")
    parser.add_argument("--config", "-c", type=Path, default=Path("config.txt"))
    parser.add_argument("--alto", action="store_true")
    parser.add_argument("--xpaths", type=Path, default=None)
    parser.add_argument("--xsd", type=str, default=None)

    args = parser.parse_args()

    if args.config and args.config.exists():
        config = configparser.ConfigParser()
        with open(args.config, 'r', encoding='utf-8') as f:
            cleaned_lines = ['[DEFAULT]\n']
            for line in f:
                cleaned_line = re.sub(r'^\[.*?\]\s*', '', line.strip())
                if cleaned_line and not cleaned_line.startswith('#'):
                    cleaned_lines.append(cleaned_line + '\n')

        config.read_string(''.join(cleaned_lines))
        defaults = config['DEFAULT']

        if args.input_path is None and 'input_path' in defaults: args.input_path = Path(defaults['input_path'])
        if args.output is None and 'output' in defaults: args.output = Path(defaults['output'])
        if args.source_lang == 'cs' and 'source_lang' in defaults: args.source_lang = defaults['source_lang']
        if args.target_lang == 'en' and 'target_lang' in defaults: args.target_lang = defaults['target_lang']
        if args.xpaths is None and 'fields' in defaults: args.xpaths = Path(defaults['fields'])

    return args


def generate_output_path(input_file, base_output, args, is_batch=False):
    if input_file.name.endswith(".alto.xml"):
        base_name = input_file.name[:-9]
        new_filename = f"{base_name}_{args.target_lang}.alto.xml"
    else:
        new_filename = f"{input_file.stem}_{args.target_lang}{input_file.suffix}"

    if is_batch:
        return base_output / new_filename
    if base_output:
        if base_output.is_dir(): return base_output / new_filename
        return base_output
    return input_file.with_name(new_filename)


def main():
    args = parse_arguments()

    print(f"\n{'=' * 60}\n ATRIUM XML TRANSLATOR ".center(60, "=") + f"\n{'=' * 60}")
    input_path = args.input_path

    if not input_path or (not input_path.is_dir() and not input_path.is_file()):
        print(f"[ERROR] Input path does not exist. Please provide a valid file or directory.")
        return

    if not args.alto and not args.xpaths:
        print("[ERROR] Specify either the --alto flag or provide --xpaths file in config.")
        return

    translator = LindatTranslator()

    # Initialize FastText Identifier ONLY if 'auto' is selected to save memory
    identifier = LanguageIdentifier() if args.source_lang == "auto" else None

    xpaths_list = []
    if args.xpaths and args.xpaths.exists():
        with open(args.xpaths, 'r', encoding='utf-8') as f:
            xpaths_list = [line.strip() for line in f if line.strip() and not line.startswith('#')]

    files_to_process = []

    # Create a temporary directory for downloads if a URL list is provided
    download_dir = args.output if args.output else Path.cwd() / f"translated_{args.target_lang}"
    download_dir.mkdir(parents=True, exist_ok=True)

    if input_path.is_file() and input_path.suffix == '.txt':
        print("[INFO] Text file detected. Reading URLs...")
        with open(input_path, 'r', encoding='utf-8') as f:
            urls = [line.strip() for line in f if line.strip() and line.startswith('http')]

        input_save_dir = Path("./my_documents")
        input_save_dir.mkdir(parents=True, exist_ok=True)

        for url in urls:
            print(f"[INFO] Downloading: {url}")
            local_file = fetch_xml_from_url(url, input_save_dir)
            if local_file:
                files_to_process.append(local_file)
    elif input_path.is_dir():
        files_to_process = [f for f in input_path.rglob('*.xml') if f.is_file()]
    else:
        files_to_process = [input_path]


    if not files_to_process:
        print(f"[WARN] No valid XML files found.")
        return

    is_batch = input_path.is_dir() or (input_path.suffix == '.txt')
    out_dir = args.output if args.output else Path.cwd() / f"translated_{args.target_lang}"
    out_dir.mkdir(parents=True, exist_ok=True)

    for i, file_path in enumerate(files_to_process, 1):
        print(f"\n[FILE {i}/{len(files_to_process)}] Processing: {file_path.name}")
        output_file = generate_output_path(file_path, out_dir, args, is_batch=is_batch)

        csv_path = output_file.with_name(f"{file_path.name.split('.')[0]}_log.csv")
        with open(csv_path, "w", encoding="utf-8", newline="") as csv_file:
            csv_writer = csv.writer(csv_file)
            csv_writer.writerow(
                ["file", "page_num", "line_num", f"text_{args.source_lang}", f"text_{args.target_lang}"])

            try:
                if args.alto:
                    process_alto_xml(file_path, output_file, translator, args.source_lang, args.target_lang, csv_writer,
                                     identifier)
                else:
                    process_amcr_xml(file_path, output_file, xpaths_list, translator, args.source_lang,
                                     args.target_lang, args.xsd, csv_writer, identifier)
            except Exception as e:
                print(f"[ERROR] Failed processing {file_path.name}: {e}")

    print(f"\n{'=' * 60}\n PROCESSING COMPLETE ".center(60, "=") + f"\n{'=' * 60}\n")


if __name__ == "__main__":
    main()