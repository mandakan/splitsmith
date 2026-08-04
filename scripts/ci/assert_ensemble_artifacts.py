"""The shipped ensemble ONNX artifacts must load under the resolved onnxruntime.

Voters C and E ship as ONNX graphs (#649). Before that they were pickled
sklearn estimators, coupled to whatever sklearn the *installing user*
resolved rather than to ``uv.lock`` -- which is how a sklearn 1.9
incompatibility reached a release: CI installed the wheel with a fresh
resolve, but the only detection it ran exercised the envelope detector
(voter A), which never touches them. The pickles are gone, so what is
left to check is that the graphs instantiate under the resolved
onnxruntime and that their declared feature width still matches the
calibration the rest of the pipeline is built from.

Cheap on purpose -- it needs neither ``fetch-models`` nor ffmpeg.
``Runtime.artifacts_dir`` defaults to the packaged ``splitsmith/data/``,
so these files come out of the wheel itself; the 440 MiB R2 download is
for the CLAP / PANN ONNX models, which this does not touch. That is what
lets it run on every PR rather than only behind the opt-in smoke job.

Run with the interpreter whose resolve you want to test:

    slim-venv/bin/python scripts/ci/assert_ensemble_artifacts.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def check_session(path: Path, label: str, *, expect_features: int | None) -> str | None:
    """Return an error message, or ``None`` when the artifact checks out.

    ``expect_features`` is the input width the calibration promises. It
    is ``None`` for the voter E probe: the calibration does not record
    the CLIP embedding width, so all that can be asserted there is that
    the session loads and takes a 2-D input.
    """
    import onnxruntime

    if not path.is_file():
        return f"{label} artifact is missing: {path}"
    try:
        session = onnxruntime.InferenceSession(str(path), providers=["CPUExecutionProvider"])
    except Exception as exc:  # noqa: BLE001 -- report any load failure
        return f"{label} artifact did not load: {type(exc).__name__}: {exc}"

    inputs = session.get_inputs()
    if len(inputs) != 1:
        names = ", ".join(spec.name for spec in inputs)
        return f"{label} artifact declares {len(inputs)} inputs ({names}); expected exactly 1"

    shape = inputs[0].shape
    if len(shape) != 2:
        return f"{label} artifact input {inputs[0].name!r} has shape {shape}; expected 2-D"
    if expect_features is not None and shape[1] != expect_features:
        return (
            f"{label} artifact takes {shape[1]} features; calibration's "
            f"voter_c_feature_dim is {expect_features}"
        )
    return None


def main() -> int:
    import onnxruntime

    from splitsmith.runtime import runtime

    print(f"onnxruntime {onnxruntime.__version__}")

    # ``artifacts_dir`` rather than ``runtime().artifact(...)``: the
    # latter raises FileNotFoundError on a miss, so a missing file would
    # crash with a traceback instead of the one-line diagnosis below.
    artifacts_dir = runtime().artifacts_dir
    calibration_path = artifacts_dir / "ensemble_calibration.json"
    try:
        calibration = json.loads(calibration_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 -- report any read/parse failure
        print(f"calibration did not parse: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    voter_c_artifacts = calibration.get("voter_c_onnx_artifacts")
    if not voter_c_artifacts:
        print(
            "calibration names no voter_c_onnx_artifacts -- rebuild the "
            "artifact set with scripts/build_ensemble_artifacts.py",
            file=sys.stderr,
        )
        return 1

    feature_dim = calibration.get("voter_c_feature_dim")
    if not isinstance(feature_dim, int):
        print(f"calibration has no usable voter_c_feature_dim: {feature_dim!r}", file=sys.stderr)
        return 1

    for camera_class, filename in sorted(voter_c_artifacts.items()):
        err = check_session(
            artifacts_dir / filename,
            f"voter C ({camera_class})",
            expect_features=feature_dim,
        )
        if err is not None:
            print(err, file=sys.stderr)
            return 1

    # The probe is genuinely optional -- artifacts built without Voter E
    # name no file, and the ensemble falls back to the 4-voter path.
    probe_name = calibration.get("voter_e_probe_artifact")
    if probe_name:
        probe = artifacts_dir / probe_name
        if probe.is_file():
            err = check_session(probe, "voter E probe", expect_features=None)
            if err is not None:
                print(err, file=sys.stderr)
                return 1

    print("ensemble artifacts load clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
