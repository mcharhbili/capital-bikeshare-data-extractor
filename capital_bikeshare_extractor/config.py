from pathlib import Path

import yaml


CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"

with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    config = yaml.safe_load(f)

S3_LIST_URL = config['s3_list_url']

S3_OBJECT_URL = config['s3_object_url']

GBFS_STATION_INFORMATION_URL = config['gbfs_station_information_url']

SUPPORTED_OUTPUT_FORMATS = config["supported_output_formats"]

BACKEND_EXTENSIONS = {
    "sqlite": config["backend_extensions"]['sqlite'],
}

DEFAULT_DATA_DIR = Path("data")
DEFAULT_MANIFEST_PATH = DEFAULT_DATA_DIR / "manifest.json"
DEFAULT_DB_PATH = DEFAULT_DATA_DIR / "db.sqlite"

MANIFEST_TABLE_NAME = config["manifest_table_name"]

COLUMN_RENAMES = config["column_renames"]
COLUMN_RENAMES_TRIP_RAW = COLUMN_RENAMES["trips_raw_data"]


