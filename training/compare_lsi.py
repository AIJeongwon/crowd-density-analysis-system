"""동일 시험 자료에서 입력별 고정 기준과 검증 자료로 보정한 기준을 비교합니다."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from calibrate_lsi import choose_threshold
from evaluate_lsi import load_labeled_predictions, summarize_predictions


def compare_pair(validation: Path, test: Path, data: Path) -> dict:
    metadata = [json.loads((path / "metrics.json").read_text(encoding="utf-8"))
                for path in (validation, test)]
    if [row["split"] for row in metadata] != ["val", "test"]:
        raise ValueError("검증 자료, 시험 자료 순서로 지정해야 합니다.")
    for key in ("weights_sha256", "input_shape", "nms_iou", "match_iou", "precision_mode"):
        if metadata[0][key] != metadata[1][key]:
            raise ValueError(f"검증 자료와 시험 자료의 평가 설정이 다릅니다: {key}")
    frames = []
    for path, row in zip((validation, test), metadata):
        predictions = json.loads((path / "predictions.json").read_text(encoding="utf-8"))
        labeled = load_labeled_predictions(data, predictions, row["split"])
        if len(labeled) != row["images"]:
            raise ValueError("평가 자료의 영상 수가 기록과 다릅니다.")
        frames.append(labeled)
    sweep = [{"confidence": step / 100, "counts": summarize_predictions(frames[0], step / 100)}
             for step in range(25, 96, 5)]
    try:
        selected = choose_threshold(sweep, 0.95)
    except ValueError:
        selected = None
    return {"validation_directory": validation.as_posix(), "test_directory": test.as_posix(),
            "weights_sha256": metadata[0]["weights_sha256"], "input_shape": metadata[0]["input_shape"],
            "test_images": len(frames[1]), "test_ap50": metadata[1]["ap50"],
            "test_ap50_95": metadata[1]["ap50_95"],
            "test_at_025": summarize_predictions(frames[1], 0.25),
            "test_at_075": summarize_predictions(frames[1], 0.75),
            "validation_sweep": sweep, "selected_on_validation": selected,
            "test_calibrated": summarize_predictions(frames[1], selected["confidence"]) if selected else None}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pair", nargs=3, action="append", required=True,
                        metavar=("NAME", "VALIDATION", "TEST"))
    parser.add_argument("--data", type=Path, default=Path("data/private/lsi-fir"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    names = [pair[0] for pair in args.pair]
    if len(set(names)) != len(names):
        parser.error("비교 항목 이름이 중복되었습니다.")
    if args.output.exists():
        parser.error("기존 비교 결과를 덮어쓰지 않습니다.")
    report = {name: compare_pair(Path(validation), Path(test), args.data)
              for name, validation, test in args.pair}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    for name, row in report.items():
        calibrated = row["test_calibrated"]
        print(json.dumps({"name": name, "ap50": row["test_ap50"],
                          "fixed_075": row["test_at_075"],
                          "calibrated_confidence": row["selected_on_validation"]["confidence"]
                          if calibrated else None, "calibrated": calibrated}))


if __name__ == "__main__":
    main()
