import argparse
import json
from pathlib import Path

from capital_bikeshare_extractor import funcs
from capital_bikeshare_extractor.config import (
    SUPPORTED_OUTPUT_FORMATS,
    DEFAULT_MANIFEST_PATH,
    DEFAULT_DB_PATH,
)
from capital_bikeshare_extractor.validation import _assert_period


def _period(value):
    try:
        _assert_period(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error
    return value


def _display_records(records, output_format):
    if output_format == "json":
        print(json.dumps(records, indent=2, default=str))
    elif output_format == "dict":
        print(records)
    else:
        funcs.display_s3_available_files(records, "print")


def _add_output_format_argument(parser):
    parser.add_argument(
        "-o",
        "--output-format",
        choices=sorted(SUPPORTED_OUTPUT_FORMATS),
        default="print",
        help="How to display records (default: print).",
    )


def _add_processing_arguments(parser):
    parser.add_argument(
        "-d",
        "--destination",
        type=Path,
        default=DEFAULT_DB_PATH,
        help=f"SQLite database file (default: {DEFAULT_DB_PATH}).",
    )
    parser.add_argument(
        "-t",
        "--temp-path",
        type=Path,
        default=Path(".temp"),
        help="Temporary download and extraction folder (default: .temp).",
    )


def _add_dry_run_argument(parser):
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be synchronized without downloading or processing anything.",
    )


def _display_dry_run(frame, output_format):
    records = frame.to_dict(orient="records")
    if not records:
        print("Dry run: nothing to synchronize.")
        return
    _display_records(records, output_format)
    print(f"Dry run: {len(records)} file(s) would be synchronized.")


def _handle_list_files(arguments):
    records = funcs.fetch_s3_available_files()
    _display_records(records, arguments.output_format)


def _handle_inspect_manifest(arguments):
    funcs.inspect_changes_manifest(
        DEFAULT_MANIFEST_PATH,
        arguments.create,
        arguments.output_format,
    )


def _handle_show_manifest(arguments):
    manifest = funcs.get_manifest(DEFAULT_MANIFEST_PATH)
    _display_records(manifest.to_dict(orient="records"), arguments.output_format)


def _handle_periods(arguments):
    if arguments.granularity == "yearly":
        periods = funcs.get_yearly_periods(DEFAULT_MANIFEST_PATH)
    else:
        periods = funcs.get_monthly_periods(DEFAULT_MANIFEST_PATH)

    values = [str(period) for period in periods]
    if arguments.output_format == "json":
        print(json.dumps(values, indent=2))
    else:
        for value in values:
            print(value)


def _handle_plan_period(arguments):
    frame = funcs.get_list_update_period(
        DEFAULT_MANIFEST_PATH,
        arguments.period,
        arguments.force,
    )
    _display_records(frame.to_dict(orient="records"), arguments.output_format)


def _handle_plan_range(arguments):
    frame = funcs.get_list_update_range(
        DEFAULT_MANIFEST_PATH,
        arguments.start,
        arguments.end,
        arguments.force,
    )
    _display_records(frame.to_dict(orient="records"), arguments.output_format)


def _handle_init_db(arguments):
    connection = funcs.create_sqlite_db_if_not_exists(arguments.db_path)
    connection.close()
    print(arguments.db_path)


def _handle_download(arguments):
    print(funcs.download(arguments.key, arguments.temp_path))


def _handle_extract(arguments):
    print(funcs.extract(arguments.key, arguments.temp_path))


def _handle_process(arguments):
    funcs.process(
        DEFAULT_MANIFEST_PATH,
        arguments.destination,
        arguments.key,
        arguments.granularity,
        arguments.period.replace("-", ""),
        arguments.temp_path,
    )
    print(f"Processed {arguments.period}.")


def _handle_sync_period(arguments):
    result = funcs.sync_period(
        arguments.destination,
        DEFAULT_MANIFEST_PATH,
        arguments.period,
        arguments.temp_path,
        arguments.force,
        arguments.dry_run,
    )
    if arguments.dry_run:
        _display_dry_run(result, arguments.output_format)
    else:
        print(f"Synchronized {arguments.period}.")


def _handle_sync(arguments):
    period = None
    if arguments.latest:
        period = funcs.get_latest_period(DEFAULT_MANIFEST_PATH, arguments.force)

    if period is not None:
        result = funcs.sync_period(
            arguments.destination,
            DEFAULT_MANIFEST_PATH,
            period,
            arguments.temp_path,
            arguments.force,
            arguments.dry_run,
        )
        if arguments.dry_run:
            _display_dry_run(result, arguments.output_format)
        else:
            print(f"Synchronized latest period {period}.")
        return

    result = funcs.sync(
        arguments.destination,
        DEFAULT_MANIFEST_PATH,
        arguments.temp_path,
        arguments.force,
        arguments.dry_run,
    )
    if arguments.dry_run:
        _display_dry_run(result, arguments.output_format)
    else:
        print("Synchronized all files.")


def _handle_sync_range(arguments):
    result = funcs.sync_range(
        arguments.destination,
        DEFAULT_MANIFEST_PATH,
        arguments.start,
        arguments.end,
        arguments.temp_path,
        arguments.force,
        arguments.dry_run,
    )
    if arguments.dry_run:
        _display_dry_run(result, arguments.output_format)
    else:
        print(f"Synchronized {arguments.start} through {arguments.end}.")


def _handle_sync_stations(arguments):
    count = funcs.sync_stations(arguments.destination)
    print(f"Synchronized {count} stations.")


def build_parser():
    parser = argparse.ArgumentParser(
        prog="capital-bikeshare",
        description="Download and process Capital Bikeshare trip data.",
    )
    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
        title="commands",
        description="Use '<command> --help' for command-specific options.",
    )

    list_parser = subparsers.add_parser(
        "list-files",
        aliases=["show-files"],
        help="List ZIP files available in the Capital Bikeshare S3 bucket.",
    )
    _add_output_format_argument(list_parser)
    list_parser.set_defaults(handler=_handle_list_files)

    inspect_parser = subparsers.add_parser(
        "inspect-manifest",
        aliases=["inspect-changes"],
        help="Create or update a JSON manifest from the S3 file listing.",
    )
    inspect_parser.add_argument(
        "--create",
        action="store_true",
        help="Create the manifest when it does not exist.",
    )
    _add_output_format_argument(inspect_parser)
    inspect_parser.set_defaults(handler=_handle_inspect_manifest)

    show_manifest_parser = subparsers.add_parser(
        "show-manifest",
        help="Display records from a JSON manifest.",
    )
    _add_output_format_argument(show_manifest_parser)
    show_manifest_parser.set_defaults(handler=_handle_show_manifest)

    periods_parser = subparsers.add_parser(
        "periods",
        help="List yearly or monthly periods in a manifest.",
    )
    periods_parser.add_argument(
        "--granularity",
        choices=("yearly", "monthly"),
        required=True,
        help="Period level to list.",
    )
    _add_output_format_argument(periods_parser)
    periods_parser.set_defaults(handler=_handle_periods)

    plan_period_parser = subparsers.add_parser(
        "plan-period",
        help="Show manifest records selected for one period.",
    )
    plan_period_parser.add_argument(
        "--period",
        required=True,
        type=_period,
        help="Year (YYYY) or month (YYYY-MM) to plan.",
    )
    plan_period_parser.add_argument(
        "--force",
        action="store_true",
        help="Include files already marked as processed.",
    )
    _add_output_format_argument(plan_period_parser)
    plan_period_parser.set_defaults(handler=_handle_plan_period)

    plan_range_parser = subparsers.add_parser(
        "plan-range",
        help="Show manifest records selected for a period range.",
    )
    plan_range_parser.add_argument(
        "--start",
        required=True,
        type=_period,
        help="First year (YYYY) or month (YYYY-MM) in the range.",
    )
    plan_range_parser.add_argument(
        "--end",
        required=True,
        type=_period,
        help="Last year (YYYY) or month (YYYY-MM) in the range.",
    )
    plan_range_parser.add_argument(
        "--force",
        action="store_true",
        help="Include files already marked as processed.",
    )
    _add_output_format_argument(plan_range_parser)
    plan_range_parser.set_defaults(handler=_handle_plan_range)

    init_db_parser = subparsers.add_parser(
        "init-db",
        help="Create the SQLite database and raw_trips table if needed.",
    )
    init_db_parser.add_argument(
        "--db-path",
        type=Path,
        default=DEFAULT_DB_PATH,
        help=f"SQLite database path (default: {DEFAULT_DB_PATH}).",
    )
    init_db_parser.set_defaults(handler=_handle_init_db)

    download_parser = subparsers.add_parser(
        "download",
        help="Download one S3 ZIP object.",
    )
    download_parser.add_argument("key", help="S3 object key to download.")
    download_parser.add_argument(
        "-t",
        "--temp-path",
        type=Path,
        default=Path(".temp"),
        help="Temporary download folder (default: .temp).",
    )
    download_parser.set_defaults(handler=_handle_download)

    extract_parser = subparsers.add_parser(
        "extract",
        help="Extract one previously downloaded ZIP object.",
    )
    extract_parser.add_argument("key", help="S3 object key to extract.")
    extract_parser.add_argument(
        "-t",
        "--temp-path",
        type=Path,
        default=Path(".temp"),
        help="Temporary download and extraction folder (default: .temp).",
    )
    extract_parser.set_defaults(handler=_handle_extract)

    process_parser = subparsers.add_parser(
        "process",
        help="Download and process one manifest object.",
    )
    _add_processing_arguments(process_parser)
    process_parser.add_argument("--key", required=True, help="S3 object key to process.")
    process_parser.add_argument(
        "--granularity",
        choices=("yearly", "monthly"),
        required=True,
        help="Period level represented by the object.",
    )
    process_parser.add_argument(
        "--period",
        required=True,
        type=_period,
        help="Year (YYYY) or month (YYYY-MM) represented by the object.",
    )
    process_parser.set_defaults(handler=_handle_process)

    sync_period_parser = subparsers.add_parser(
        "sync-period",
        help="Synchronize one year or month from the JSON manifest.",
    )
    _add_processing_arguments(sync_period_parser)
    sync_period_parser.add_argument(
        "--period",
        required=True,
        type=_period,
        help="Year (YYYY) or month (YYYY-MM) to synchronize.",
    )
    sync_period_parser.add_argument(
        "--force",
        action="store_true",
        help="Reprocess files already marked as processed.",
    )
    _add_dry_run_argument(sync_period_parser)
    _add_output_format_argument(sync_period_parser)
    sync_period_parser.set_defaults(handler=_handle_sync_period)

    sync_parser = subparsers.add_parser(
        "sync",
        help="Synchronize every pending file from the JSON manifest.",
    )
    _add_processing_arguments(sync_parser)
    sync_parser.add_argument(
        "--force",
        action="store_true",
        help="Reprocess files already marked as processed.",
    )
    sync_parser.add_argument(
        "--latest",
        action="store_true",
        help="Synchronize only the single newest pending period instead of everything pending.",
    )
    _add_dry_run_argument(sync_parser)
    _add_output_format_argument(sync_parser)
    sync_parser.set_defaults(handler=_handle_sync)

    sync_range_parser = subparsers.add_parser(
        "sync-range",
        help="Synchronize an inclusive range from the JSON manifest.",
    )
    _add_processing_arguments(sync_range_parser)
    sync_range_parser.add_argument(
        "--start",
        required=True,
        type=_period,
        help="First year (YYYY) or month (YYYY-MM) in the range.",
    )
    sync_range_parser.add_argument(
        "--end",
        required=True,
        type=_period,
        help="Last year (YYYY) or month (YYYY-MM) in the range.",
    )
    sync_range_parser.add_argument(
        "--force",
        action="store_true",
        help="Reprocess files already marked as processed.",
    )
    _add_dry_run_argument(sync_range_parser)
    _add_output_format_argument(sync_range_parser)
    sync_range_parser.set_defaults(handler=_handle_sync_range)

    sync_stations_parser = subparsers.add_parser(
        "sync-stations",
        help="Fetch the current station location snapshot from the GBFS feed and upsert it into the database.",
    )
    sync_stations_parser.add_argument(
        "-d",
        "--destination",
        type=Path,
        default=DEFAULT_DB_PATH,
        help=f"SQLite database file (default: {DEFAULT_DB_PATH}).",
    )
    sync_stations_parser.set_defaults(handler=_handle_sync_stations)

    return parser


def _format_error_chain(error):
    """Render an exception together with its __cause__ chain.

    Some errors (e.g. pandas' DatabaseError("Execution failed")) carry no
    useful message of their own and only make sense alongside the original
    exception they wrap.
    """
    parts = [f"{type(error).__name__}: {error}"]
    cause = error.__cause__
    while cause is not None:
        parts.append(f"caused by {type(cause).__name__}: {cause}")
        cause = cause.__cause__
    return "\n  ".join(parts)


def run(argv=None):
    parser = build_parser()
    arguments = parser.parse_args(argv)

    try:
        arguments.handler(arguments)
    except Exception as error:
        parser.exit(1, f"error: {_format_error_chain(error)}\n")

    return 0


if __name__ == "__main__":
    run()
