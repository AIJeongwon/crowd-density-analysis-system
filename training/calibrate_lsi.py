"""검증 자료에서만 정밀도 목표를 만족하는 신뢰도 기준을 선정합니다."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from evaluate_lsi import load_labeled_predictions, summarize_predictions


def validate_policy(policy: dict, metadata: dict) -> float:
    if policy["selected_on"] != "val" or policy["weights_sha256"] != metadata["weights_sha256"]:
        raise ValueError("해당 가중치의 검증 자료에서 선정한 기준이 아닙니다.")
    # 입력 크기 선택 기능 이전의 보정 파일은 160 정사각형 평가에서만 생성되었습니다.
    if policy.get("input_shape", [1, 3, 160, 160]) != metadata["input_shape"]:
        raise ValueError("신뢰도 기준을 선정한 입력 크기와 평가 입력 크기가 다릅니다.")
    confidence = policy["selected"]["confidence"]
    if not 0 < confidence < 1:
        raise ValueError("저장된 신뢰도 기준이 유효하지 않습니다.")
    return confidence


def choose_threshold(rows: list[dict], minimum_precision: float) -> dict:
    eligible = [row for row in rows if row["counts"]["precision"] is not None
                and row["counts"]["precision"] >= minimum_precision
                and row["counts"]["true_positives"] > 0]
    if not eligible:
        raise ValueError("검증 자료에서 목표 정밀도를 만족하는 기준이 없습니다.")
    return max(eligible, key=lambda row: (row["counts"]["recall"],
                                         row["counts"]["precision"], -row["confidence"]))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evaluation", type=Path, required=True)
    parser.add_argument("--data", type=Path, default=Path("data/private/lsi-fir"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--minimum-precision", type=float, default=0.95)
    parser.add_argument("--policy", type=Path, help="이미 선정한 기준을 적용할 때만 지정합니다.")
    args = parser.parse_args()
    metadata = json.loads((args.evaluation / "metrics.json").read_text(encoding="utf-8"))
    if args.policy is None and metadata["split"] != "val":
        parser.error("시험 자료로 신뢰도 기준을 선정할 수 없습니다.")
    if not 0 < args.minimum_precision <= 1:
        parser.error("목표 정밀도는 0 초과 1 이하여야 합니다.")
    if args.output.exists():
        parser.error("기존 보정 결과를 덮어쓰지 않습니다.")
    predictions = json.loads((args.evaluation / "predictions.json").read_text(encoding="utf-8"))
    frames = load_labeled_predictions(args.data, predictions, metadata["split"])
    if len(frames) != metadata["images"]:
        parser.error("평가 결과와 검증 자료의 영상 수가 다릅니다.")
    if args.policy:
        policy = json.loads(args.policy.read_text(encoding="utf-8"))
        try:
            confidence = validate_policy(policy, metadata)
        except ValueError as exc:
            parser.error(str(exc))
        best = {"confidence": confidence, "counts": summarize_predictions(frames, confidence)}
        report = {"selected_on": "val", "applied_to": metadata["split"],
                  "weights_sha256": metadata["weights_sha256"],
                  "input_shape": metadata["input_shape"], "selected": best}
    else:
        rows = [{"confidence": step / 100, "counts": summarize_predictions(frames, step / 100)}
                for step in range(25, 96, 5)]
        best = choose_threshold(rows, args.minimum_precision)
        report = {"selected_on": "val", "weights_sha256": metadata["weights_sha256"],
                  "input_shape": metadata["input_shape"],
                  "minimum_precision": args.minimum_precision, "selected": best, "sweep": rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(best, indent=2))


if __name__ == "__main__":
    main()
