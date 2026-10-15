from pathlib import Path
import re

import nibabel as nib
import pandas as pd


DATA_DIR = Path("processed/mri/ixi/t1")
SELECTED_CSV = Path("metadata/mri/ixi_selected_subjects.csv")
MATCHED_CSV = Path("metadata/mri/ixi_t1_matched.csv")
OUTPUT_CSV = Path("metadata/mri/ixi_qc.csv")
CATALOG_CSV = Path("metadata/mri/ixi_catalog.csv")


def parse_site(filename: str) -> str:
    parts = filename.split("-")
    return parts[1] if len(parts) >= 2 else ""


def parse_subject_id(filename: str) -> int | None:
    match = re.match(r"IXI(\d{3})-", filename)
    return int(match.group(1)) if match else None


def main():
    selected = pd.read_csv(SELECTED_CSV)
    matched = pd.read_csv(MATCHED_CSV)

    selected["IXI_ID"] = selected["IXI_ID"].astype(int)
    matched["IXI_ID"] = matched["IXI_ID"].astype(int)

    records = []

    for path in sorted(DATA_DIR.glob("*.nii.gz")):
        subject_id = parse_subject_id(path.name)

        record = {
            "IXI_ID": subject_id,
            "filename": path.name,
            "file_path": path.as_posix(),
            "site": parse_site(path.name),
            "file_size_mb": round(path.stat().st_size / (1024 * 1024), 2),
            "load_ok": False,
            "shape_x": None,
            "shape_y": None,
            "shape_z": None,
            "voxel_x_mm": None,
            "voxel_y_mm": None,
            "voxel_z_mm": None,
            "datatype": None,
            "orientation": None,
            "finite_data": False,
            "nonzero_voxels": None,
            "qc_status": "FAIL",
            "qc_notes": "",
        }

        try:
            img = nib.load(str(path))
            header = img.header

            shape = img.shape
            zooms = header.get_zooms()

            record["load_ok"] = True

            if len(shape) >= 3:
                record["shape_x"] = int(shape[0])
                record["shape_y"] = int(shape[1])
                record["shape_z"] = int(shape[2])

            if len(zooms) >= 3:
                record["voxel_x_mm"] = float(zooms[0])
                record["voxel_y_mm"] = float(zooms[1])
                record["voxel_z_mm"] = float(zooms[2])

            record["datatype"] = str(header.get_data_dtype())
            record["orientation"] = "".join(
                nib.aff2axcodes(img.affine)
            )

            data = img.get_fdata(dtype="float32")

            record["finite_data"] = bool(
                pd.notna(data).all()
            )

            record["nonzero_voxels"] = int(
                (data != 0).sum()
            )

            problems = []

            if len(shape) != 3:
                problems.append(f"unexpected dimensions: {shape}")

            if any(dim <= 0 for dim in shape[:3]):
                problems.append("invalid image dimensions")

            if any(v <= 0 for v in zooms[:3]):
                problems.append("invalid voxel size")

            if not record["finite_data"]:
                problems.append("NaN/Inf values present")

            if record["nonzero_voxels"] == 0:
                problems.append("empty image")

            if problems:
                record["qc_notes"] = "; ".join(problems)
            else:
                record["qc_status"] = "PASS"
                record["qc_notes"] = "Basic NIfTI integrity checks passed"

        except Exception as exc:
            record["qc_notes"] = str(exc)

        records.append(record)

    qc = pd.DataFrame(records)
    qc.to_csv(OUTPUT_CSV, index=False)

    catalog = (
        selected.merge(
            matched[["IXI_ID", "ArchiveFile", "Status"]],
            on="IXI_ID",
            how="left",
        )
        .merge(
            qc,
            on="IXI_ID",
            how="left",
        )
    )

    catalog["dataset"] = "IXI"
    catalog["subject_type"] = "healthy_reference"
    catalog["modality"] = "T1"
    catalog["format"] = "NIfTI"
    catalog["license"] = "CC BY-SA 3.0"

    catalog.to_csv(CATALOG_CSV, index=False)

    print("=" * 50)
    print("IXI MRI QC SUMMARY")
    print("=" * 50)
    print("Selected subjects:", len(selected))
    print("Files found:", len(qc))
    print("QC PASS:", int((qc["qc_status"] == "PASS").sum()))
    print("QC FAIL:", int((qc["qc_status"] == "FAIL").sum()))
    print()
    print("Sites:")
    print(qc["site"].value_counts().to_string())
    print()
    print("Image shapes:")
    print(
        qc[
            ["shape_x", "shape_y", "shape_z"]
        ]
        .value_counts()
        .to_string()
    )
    print()
    print("Voxel sizes:")
    print(
        qc[
            ["voxel_x_mm", "voxel_y_mm", "voxel_z_mm"]
        ]
        .value_counts()
        .to_string()
    )
    print()
    print("Orientations:")
    print(qc["orientation"].value_counts().to_string())
    print("=" * 50)


if __name__ == "__main__":
    main()