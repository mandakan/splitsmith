"""The shipped ensemble artifacts must load under the resolved sklearn.

The joblib artifacts are pickled sklearn estimators, coupled to whatever
sklearn the *installing user* resolves rather than to ``uv.lock``. This
is how a sklearn 1.9 incompatibility reached a release: CI installed the
wheel with a fresh resolve, but the only detection it ran exercised the
envelope detector (voter A), which never touches them.

Cheap on purpose -- it needs neither ``fetch-models`` nor ffmpeg.
``Runtime.artifacts_dir`` defaults to the packaged ``splitsmith/data/``,
so these files come out of the wheel itself; the 440 MiB R2 download is
for the CLAP / PANN ONNX models, which this does not touch. That is what
lets it run on every PR rather than only behind the opt-in smoke job.

Run with the interpreter whose resolve you want to test:

    slim-venv/bin/python scripts/ci/assert_ensemble_artifacts.py
"""

from __future__ import annotations

import sys
import warnings


def main() -> int:
    import sklearn
    from sklearn.exceptions import InconsistentVersionWarning

    from splitsmith.ensemble.calibration import load_voter_c_model
    from splitsmith.runtime import runtime

    print(f"sklearn {sklearn.__version__}")

    # A cross-version unpickle that sklearn calls possibly-invalid is a
    # failure, not a warning -- silently wrong detection beats a crash
    # for nobody.
    with warnings.catch_warnings():
        warnings.simplefilter("error", InconsistentVersionWarning)
        try:
            load_voter_c_model()
        except InconsistentVersionWarning as exc:
            print(f"voter C artifact is cross-version: {exc}", file=sys.stderr)
            return 1
        except Exception as exc:  # noqa: BLE001 -- report any load failure
            print(f"voter C artifact did not load: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1

        # ``artifacts_dir`` rather than ``runtime().artifact(...)``: the
        # latter raises FileNotFoundError on a miss, so the original
        # inline sentinel's ``if probe.exists()`` could never be False
        # and an absent probe would have crashed this check instead of
        # skipping it. The probe is genuinely optional.
        probe = runtime().artifacts_dir / "voter_e_visual_probe.joblib"
        if probe.is_file():
            import joblib

            try:
                joblib.load(probe)
            except InconsistentVersionWarning as exc:
                print(f"voter E probe is cross-version: {exc}", file=sys.stderr)
                return 1
            except Exception as exc:  # noqa: BLE001 -- report any load failure
                print(f"voter E probe did not load: {type(exc).__name__}: {exc}", file=sys.stderr)
                return 1

    print("ensemble artifacts load clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
