# 160 입력 모델 개선 실험

## 범위

2026-09-10, [입력 크기 비교](input-size-comparison.md) 이후 160x160 입력과 기존 YOLOv5l 구조를 유지한 채 학습 방법을 비교한다. 카메라 수신, 전처리, 화면 설정, 추론 주기와 운영 모델 파일은 변경하지 않는다. 추가 데이터 수집 없이 이미 준비한 LSI 자료만 사용한다.

## 결과 요약

세 후보를 학습하고 기존과 같은 시험 영상 8,136장, 정답 박스 4,169개에서 비교했다. **객체 존재 손실 비중을 높인 후보는 사전에 정한 공통 신뢰도 0.75에서 검출률과 주석 기준 오검출 수가 함께 개선됐다.** 다만 검증 자료의 정밀도 95% 목표를 만족시키는 기준은 0.85였고, 그 기준의 시험 결과는 재현율이 떨어졌다. 현장 비교용 후보로 보존하되 운영 모델은 교체하지 않는다.

다음 표는 모두 신뢰도 0.75, NMS IoU 0.45, 정답 일치 IoU 0.5의 결과다. 박스 높이는 160 입력이 아니라 원본 영상 기준이다. 정밀도와 오검출 수는 제공된 주석과의 일치 여부이며, 아래 주석 한계를 함께 고려해야 한다.

| 지표 | 기존 LSI 160 | 크기 증강 | 학습 구성 보강 | 객체 존재 손실 조정 |
| --- | ---: | ---: | ---: | ---: |
| 정밀도 | 96.15% | 85.69% | 80.72% | 96.66% |
| 전체 재현율 | 82.73% | 86.50% | 84.26% | 86.02% |
| 높이 10~19픽셀 재현율, 140개 | 0.00% | 1.43% | 0.00% | 0.71% |
| 높이 20~39픽셀 재현율, 1,323개 | 72.49% | 79.82% | 78.16% | 77.85% |
| 높이 40픽셀 이상 재현율, 2,706개 | 92.02% | 94.16% | 91.61% | 94.42% |
| 일치 박스 | 3,449 | 3,606 | 3,513 | 3,586 |
| 불일치 박스, 주석 기준 오검출 | 138 | 602 | 839 | 124 |
| 미탐 박스 | 720 | 563 | 656 | 583 |
| 음성 목록 4,857장 중 검출 발생 영상 | 19 | 10 | 54 | 14 |
| 주석 인원수 대비 평균 절대 오차 | 0.1013명 | 0.1402명 | 0.1815명 | 0.0835명 |

객체 존재 손실 조정은 기존보다 정답 사람 137개를 더 찾고 불일치 박스는 14개 줄였다. 인원수 평균 절대 오차는 약 17.6% 감소했다. 높이 10~19픽셀은 140개 중 1개만 검출하므로 이 크기의 문제는 해결되지 않았다.

AP는 위의 고정 신뢰도 한 점이 아니라 신뢰도 전반에서 계산한 별도 지표다.

| 지표 | 기존 LSI 160 | 크기 증강 | 학습 구성 보강 | 객체 존재 손실 조정 |
| --- | ---: | ---: | ---: | ---: |
| AP50 | 91.81% | 91.94% | 90.40% | 93.17% |
| AP50-95 | 65.48% | 67.24% | 67.97% | 67.22% |

## 신뢰도 선택의 한계

검증 정밀도 95% 이상에서 재현율을 최대화하는 기존 규칙을 적용하면 다음과 같다. 시험 결과가 좋은 신뢰도로 선택 파일을 다시 쓰지 않았다.

| 후보 | 검증에서 선택한 신뢰도 | 검증 정밀도 | 검증 재현율 | 시험 정밀도 | 시험 재현율 | 시험 불일치 박스 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 기존 LSI 160 | 0.75 | 95.14% | 91.67% | 96.15% | 82.73% | 138 |
| 크기 증강 | 0.75 | 95.02% | 95.78% | 85.69% | 86.50% | 602 |
| 학습 구성 보강 | 0.85 | 98.25% | 76.71% | 98.59% | 73.54% | 44 |
| 객체 존재 손실 조정 | 0.85 | 96.28% | 94.52% | 98.38% | 78.63% | 54 |

객체 존재 손실 조정 후보의 신뢰도 0.75는 검증에서 정밀도 93.14%, 재현율 97.72%다. 따라서 시험에서의 개선만으로 0.75가 모든 환경에 적합하다고 권장할 수 없다. 0.85를 적용하면 시험 오검출은 감소하지만 재현율과 인원수 오차는 기존보다 나빠진다. 이는 임계값을 포함한 운영 정책까지 확정한 개선이 아니라, 같은 입력과 연산량에서 유망한 학습 후보를 찾은 결과다.

## 정답 주석 한계

크기 증강 후보의 불일치 박스 중 `test_03_00114`, `test_04_00204`, `test_06_02491`을 직접 확인했다. 세 영상 모두 가장자리에 실제 사람이 보이지만 그 사람의 정답 박스는 없다. 원본 `Detection/Test/annotations/`의 대응 텍스트에도 해당 박스가 없으며, 주석이 제공되지 않은 객체가 있을 수 있다는 주의문이 들어 있다. 변환 과정에서 박스가 누락된 사례는 아니다.

- 일부 실제 사람의 검출도 현재 평가에서는 오검출로 집계된다. 전체 602개가 실제 오검출이라고 단정하거나, 반대로 모두 올바른 검출이라고 간주하지 않는다.
- 원본만으로 미주석 사람의 제외 기준이나 무시 영역을 확정할 수 없어 시험 정답과 평가기는 수정하지 않았다. 일부 사례를 보고 전체 정밀도를 임의로 보정하지도 않았다.
- 인원수 오차 역시 실제 전체 인원수와의 오차가 아니라 제공된 정답 박스 개수와의 오차다.
- 다음 평가에서는 가장자리 사람, 가림과 제외 대상을 명시한 별도 정답 자료가 필요하다. 기존 시험 자료를 보완하면 개발 자료로 취급하고 새로운 촬영 구간을 독립 시험으로 남겨야 한다.

검사 영상과 좌표는 `outputs/lsi-refinement/error-examples.png`, `error-examples.json`에 보관한다. 세 사례의 표본 확인으로 미주석 사람의 전체 비율을 추정하지 않는다.

## 학습 후보

모든 후보는 같은 LLVIP 원본 가중치에서 시작한다. 기본 후보와 동일하게 SGD, 배치 32, 시드 42, 최대 20회, 조기 종료 대기 5회를 사용한다. 실제 종료 회차와 최적 가중치 선택은 검증 지표에 따라 달라진다.

| 후보 | 학습 영상 | 변경 내용 |
| --- | ---: | --- |
| 기존 LSI 160 | 3,938장 | 기존 기준선 보존 |
| 크기 증강 | 3,938장 | 크기 변형 범위 0.15에서 0.50, 모자이크 적용 확률 0에서 0.50 |
| 학습 구성 보강 | 5,127장 | 크기 증강에 작은 사람 및 오검출 배경 반복 노출, 저대비 변형 추가 |
| 객체 존재 손실 조정 | 3,938장 | 기본 증강 유지, 객체 존재 손실의 설정값만 1에서 4로 변경한 후속 후보 |

크기와 박스의 변환에는 [공식 YOLOv5 학습 로더](https://github.com/ultralytics/yolov5/blob/v7.0/utils/dataloaders.py)와 [증강 함수](https://github.com/ultralytics/yolov5/blob/v7.0/utils/augmentations.py)를 사용한다. 모자이크는 여러 학습 영상을 합치는 학습용 변형이며, 개인정보 보호를 위한 비화 처리를 뜻하지 않는다. 추론 때는 원본 한 프레임만 처리한다.

## 학습 구성 보강

- 학습 구간의 높이 10~19픽셀 사람이 포함된 영상 73장에 대해 원본 반복 2개와 저대비 변형 1개를 추가했다. 원본 사람 박스 91개가 학습 목록에서 364회 노출되지만, 독립된 원본 사례가 364개로 늘어난 것은 아니다.
- 기존 LSI 모델이 신뢰도 0.25 이상으로 오검출한, 사람 주석이 없는 학습 영상은 1장(`train_04_00974`)이었다. 같은 방식으로 반복과 저대비 변형을 추가했다. 이미 학습한 구간에서 채굴했기 때문에 새로운 환경의 어려운 배경을 충분히 확보한 실험은 아니다.
- 나머지 학습 영상은 순서상 매 네 번째 영상에 대비 0.5배 변형을 추가했다. 중간 명도 127.5를 기준으로 명암 차이를 줄이며 원본의 박스 위치와 크기는 그대로 유지한다.
- 총 1,189장의 학습용 파생 영상을 추가했다. 실제 Lepton 영상이나 다른 공개 데이터셋을 새로 수집한 결과는 아니다.
- LSI 원시값을 섭씨로 해석하거나 최소 온도폭 2도를 그대로 적용하지 않았다. 저대비 변형은 실제 장비와 일치가 입증된 보정이 아니라 대비 변화에 대한 학습 증강이다.

오검출 선별은 학습 구간 평가만 허용한다. 검증 및 시험 식별자가 섞이면 준비 도구가 중단된다. 검증 888장과 시험 8,136장의 이미지 및 라벨은 원본 그대로 복사하며 증강이나 반복 노출을 하지 않는다. 이들은 학습 데이터 경로에 포함하지 않는다.

실제 증강 로더에서 512개 입력을 표본 검사했다. 입력 픽셀 높이별 박스는 10픽셀 미만 21개, 10~19픽셀 127개, 20~39픽셀 305개, 40픽셀 이상 184개였다. 작은 사례의 학습 노출이 늘었음을 확인하는 검사이며 독립 자료의 검출 성능 측정은 아니다. 합성과 잘림으로 일부 10픽셀 미만 박스도 생기며, 이 구간이 실제로 검출 가능해졌다는 뜻이 아니다.

## 선택과 평가

각 후보의 신뢰도는 기존과 동일하게 검증 자료에서 0.25~0.95, 0.05 간격으로 탐색한다. 검증 정밀도 95% 이상을 만족하는 기준 중 재현율이 가장 높은 값을 선택한다. 후보끼리도 해당 검증 재현율을 먼저 비교하고 동률이면 정밀도로 구분한다. 기준을 만족하지 못한 후보는 선택하지 않는다.

선택과 신뢰도 기준을 저장한 뒤 기존 시험 자료에서 모든 후보를 비교한다. 고정 신뢰도 0.75와 검증에서 선택한 신뢰도 결과를 함께 기록한다. 시험 결과를 보고 신뢰도를 다시 조정하지 않는다. 이미 이전 실험에서 사용한 시험 자료이므로 새로운 독립 시험이라고 주장하지 않는다.

최소한 다음을 확인한다.

- 정밀도, 전체 재현율, AP50 및 AP50-95
- 원본 높이 10~19픽셀과 20~39픽셀의 재현율
- 전체 오검출 박스 수와 사람이 없는 영상의 오검출 발생 장수
- 영상별 인원수 평균 절대 오차
- 선택한 모델의 ONNX 변환 일치와 CPU 단일 프레임 추론 시간

10~19픽셀 검증 사례가 없는 한계는 남아 있다. 이번 후보 선택이 실제 작은 사람 성능을 직접 최적화하는 것은 아니므로, 해당 크기의 시험 성능이 좋아져도 독립된 실제 장비 영상으로 재검증해야 한다.

## 후속 손실 비중 비교

위 두 후보의 시험 결과에서 전반적인 개선을 확인하지 못한 뒤, 객체 존재 점수의 학습 손실 비중만 바꾼 후보를 추가했다. 원래 학습 자료와 기본 증강으로 돌아가고 `obj`만 1에서 4로 변경한다.

[공식 학습 코드](https://github.com/ultralytics/yolov5/blob/v7.0/train.py#L236)는 객체 손실 가중치에 `(입력 크기 / 640)^2`를 곱한다. 현재 세 검출층에서는 160 입력의 기본값이 0.0625이며, 이번 후보는 0.25가 된다. 이는 320 입력의 기본 가중치와 같다. 손실의 상대 비중이 작은 입력에 적합한지 확인하는 실험이지, 공식 규칙에 오류가 있다고 가정한 수정은 아니다. 신경망 추론 연산은 바뀌지 않는다.

앞선 시험 결과를 본 뒤 추가한 탐색 실험이라는 사실을 분리해 기록한다. 이 후보도 신뢰도는 검증 자료에서만 정한 뒤 고정하여 시험에 적용한다. 기존 선택 기록과 실패 결과를 덮어쓰지 않는다.

## 실행 확인

로컬 Ryzen 5 9600X CPU, ONNX Runtime 1.20.1, 단일 영상, FP32, 연산 스레드 4개에서 측정했다. 각 후보를 10회 예열한 뒤 30회씩 3차례 측정한 중앙값이다. 전처리, NMS, 카메라 수신과 화면 출력은 제외한다.

| 후보 | 중앙값 | 95백분위 | 모델 파일 크기 |
| --- | ---: | ---: | ---: |
| 기존 LSI 160 | 15.21 ms | 15.78 ms | 186,508,851바이트 |
| 크기 증강 | 15.12 ms | 15.71 ms | 186,508,851바이트 |
| 객체 존재 손실 조정 | 15.19 ms | 15.72 ms | 186,508,851바이트 |

모델 구조와 파일 크기가 같고 이 PC에서 처리 시간도 비슷했다. 미세한 시간 차이를 속도 향상으로 해석하지 않는다. 이는 라즈베리파이 측정값이 아니며 3 FPS 유지 여부는 장비에서 다시 확인해야 한다.

크기 증강 및 객체 존재 손실 조정 후보는 검증 영상 5장씩 PyTorch와 ONNX의 전체 출력이 `rtol=1e-3`, `atol=1e-3` 이내로 일치했다. 기존 `YoloV5OnnxDetector`에서도 5장씩 실행해 유효한 점수와 영상 경계 안의 박스를 확인했다. 새 후보는 `outputs/`에만 보관하며 기존 `models/` 파일, 실행 기본값과 라즈베리파이는 변경하지 않았다.

| 항목 | 객체 존재 손실 조정 후보 |
| --- | --- |
| PyTorch | `outputs/lsi-refinement/objectness-candidate/weights/best.pt` |
| PyTorch SHA-256 | `963c26ca4ad35865a8b57a601e2b7ae531e504b1a3967d6b222ccbeb364e14f7` |
| ONNX | `outputs/lsi-refinement/objectness-benchmark/160x160.onnx` |
| ONNX SHA-256 | `92dbeded9e7502d5febc894b5cc68c4e6053ce8028926013f5d9d142faae531a` |
| 입력 | `[1, 3, 160, 160]`, 종횡비 유지 후 패딩 |

최종 비교는 `outputs/lsi-refinement/comparison-final.json`, 검증 선택은 각 `*-policy.json`, 최초 후보 선택은 `selection.json`에 남긴다. 증강 후보는 18회, 구성 보강 후보는 19회, 객체 존재 손실 후보는 20회 학습했다. 단일 시드의 실험이며 연속 프레임도 포함되어 있어 작은 수치 차이의 통계적 유의성을 주장하지 않는다. Lepton 영상, 고온 물체, 뜨거운 아스팔트와 기존 LLVIP 영역의 성능은 이번에 검증하지 않았다.

## 재현

기존 [학습 환경](model-training.md#학습-환경)에서 실행한다. 출력 경로가 이미 존재하면 새 경로를 지정한다. 검증과 시험 평가는 일관성을 위해 기존 `data/private/lsi-fir/dataset.yaml`을 사용한다.

```powershell
python training/evaluate_lsi.py --weights outputs/lsi-training/candidate/weights/best.pt --split train --output outputs/lsi-refinement/baseline-train
python training/prepare_lsi_refinement.py --mining outputs/lsi-refinement/baseline-train --output data/private/lsi-refinement
python training/inspect_lsi_augmentation.py --data data/private/lsi-refinement/dataset.yaml --hyp training/hyp-lsi-small.yaml --output outputs/lsi-refinement/augmentation-check
python training/train_lsi.py --hyp training/hyp-lsi-small.yaml --output outputs/lsi-refinement/scale-candidate
python training/train_lsi.py --data data/private/lsi-refinement/dataset.yaml --hyp training/hyp-lsi-small.yaml --output outputs/lsi-refinement/balanced-candidate
python training/evaluate_lsi.py --weights outputs/lsi-refinement/scale-candidate/weights/best.pt --output outputs/lsi-refinement/scale-val
python training/calibrate_lsi.py --evaluation outputs/lsi-refinement/scale-val --output outputs/lsi-refinement/scale-policy.json
python training/train_lsi.py --hyp training/hyp-lsi-objectness.yaml --output outputs/lsi-refinement/objectness-candidate
python training/evaluate_lsi.py --weights outputs/lsi-refinement/objectness-candidate/weights/best.pt --output outputs/lsi-refinement/objectness-val
python training/calibrate_lsi.py --evaluation outputs/lsi-refinement/objectness-val --output outputs/lsi-refinement/objectness-policy.json
python training/evaluate_lsi.py --weights outputs/lsi-refinement/objectness-candidate/weights/best.pt --split test --output outputs/lsi-refinement/objectness-test
python training/calibrate_lsi.py --evaluation outputs/lsi-refinement/objectness-test --policy outputs/lsi-refinement/objectness-policy.json --output outputs/lsi-refinement/objectness-test-calibrated.json
python training/benchmark_lsi.py --weights outputs/lsi-refinement/objectness-candidate/weights/best.pt --shapes 160x160 --output outputs/lsi-refinement/objectness-benchmark
```

두 번째 후보도 가중치 경로와 출력 이름을 `balanced`에 맞춰 평가한다. 자료, 가중치, 예측과 학습 로그는 기존과 같이 Git 추적 대상이 아닌 폴더에만 저장한다. 데이터와 추가 학습 가중치의 배포 제한은 기존 LSI 학습 문서의 주의사항을 따른다.
