# LSI 열화상 추가 학습

## 목적과 범위

기존 LLVIP YOLOv5l을 기준선으로 보존하고, LSI-FIR 검출 데이터로 추가 학습한 후보를 별도로 만든다. 첫 후보의 입력은 160x160이며 네트워크 구조는 바꾸지 않는다. 라즈베리파이의 기본 추론 주기 3 FPS와 화면 설정도 유지한다. 후속 입력 크기 실험은 [입력 크기 비교](input-size-comparison.md)에 정리한다.

160 입력을 유지한 후속 학습과 결과는 [모델 개선 실험](model-refinement.md)에 정리한다. 시험 주석에 포함되지 않은 사람이 실제로 보이는 사례도 확인했으므로, 아래 정밀도 및 인원수 결과는 주석 기준 수치로 해석한다. 미주석 객체를 평가에서 제외하는 규칙은 아직 적용하지 않았다.

LSI는 학습 완료 모델이 아니라 [박스 주석이 포함된 공개 데이터셋](https://e-archivo.uc3m.es/entities/publication/ca4cf1d7-506c-4155-bdfd-1b2ed1e0c1aa)이다. 원본, 변환 영상, 학습 가중치와 평가 출력은 Git 추적 대상이 아닌 `data/raw/`, `data/private/`, `models/`, `outputs/` 아래에 둔다. 현재 저장소에 학습 코드만 추가해도 데이터나 가중치가 자동으로 공개되지는 않는다.

## 저장소 공개 범위

| 포함 | 제외 |
| --- | --- |
| 자료 변환, 추가 학습, 평가 및 ONNX 변환 코드 | 원본 데이터와 변환 영상 |
| 학습 설정, 테스트, 재현 명령 | PyTorch 및 ONNX 모델 파일 |
| 실험 조건, 결과 표, 해시와 남은 검증 사항 | 원시 예측, 학습 로그와 로컬 실행 환경 |

`git clone`이나 `git pull`은 코드와 문서만 받으며, 새 모델을 내려받거나 실행 중인 모델을 교체하지 않는다. 문서의 `outputs/` 경로는 실험한 PC의 로컬 결과 위치이며 공개 다운로드 주소가 아니다. 같은 도구로 실험을 재현하면 지정한 출력 경로에 결과가 생성된다.

이 문서의 실행 예시는 첫 번째 LSI 후보 기준이다. 후속 객체 존재 손실 조정 후보의 모델 경로, 비교 수치와 신뢰도 선택의 한계는 [모델 개선 실험](model-refinement.md)에 별도로 기록한다. 두 후보 모두 실제 Lepton 검증 전이며, 학습 코드 공개와 장비의 운영 모델 교체를 구분한다.

## 1차 실험 결과

2026-09-10, 로컬 PC에서 추가 학습과 평가 및 ONNX 변환을 완료했다. 최대 20회 중 13회에서 조기 종료됐으며 8번째 학습 결과(로그의 epoch 7)를 최종 가중치로 선택했다. 모델 구조, 입력 크기 및 라즈베리파이 프로그램은 변경하지 않았다. 아직 장비에 배포하지 않은 현장 테스트용 후보이며 운영 모델 승격은 보류한다.

아래는 학습에 사용하지 않은 공식 Test 중 주석이 확인된 영상 8,136장, 사람 박스 4,169개에 대한 결과다. 원본 픽셀 높이별 검출률은 재현율이며, 정밀도는 검출한 박스 중 정답의 비율이다.

| 지표 | 기존 모델, 기준 0.25 | LSI 후보, 기준 0.25 | LSI 후보, 기준 0.75 |
| --- | ---: | ---: | ---: |
| 정밀도 | 82.42% | 66.65% | 96.15% |
| 전체 재현율 | 12.93% | 91.80% | 82.73% |
| 높이 10~19픽셀 재현율, 140개 | 0.00% | 15.00% | 0.00% |
| 높이 20~39픽셀 재현율, 1,323개 | 0.00% | 88.59% | 72.49% |
| 높이 40픽셀 이상 재현율, 2,706개 | 19.92% | 97.34% | 92.02% |
| 오검출 박스 수 | 115 | 1,915 | 138 |
| 미탐 사람 박스 수 | 3,630 | 342 | 720 |

임계값 전반을 평가하는 AP50은 23.14%에서 91.81%, AP50-95는 11.17%에서 65.48%로 향상됐다. 이는 LSI 시험 부분집합의 결과이며 전체 공식 벤치마크 수치나 실제 설치 환경 성능으로 인용하지 않는다.

기준 0.25의 시험 결과에서 오검출 증가를 확인한 뒤, 검증 자료만으로 신뢰도 기준을 선정하는 단계를 추가했다. 기준 후보 0.25~0.95(0.05 간격) 중 검증 정밀도 95% 이상에서 재현율이 가장 높은 0.75를 선택했다. 시험 자료는 기준 선택 계산에 사용하지 않았으나, 동일 시험 자료를 반복 확인한 후속 분석이므로 새로운 현장 평가 자료로 다시 검증해야 한다.

높이 10~19픽셀의 문제는 해결됐다고 보기 어렵다. 기준을 낮추면 일부를 검출하지만 오검출이 크게 증가한다. 이 구간의 사례가 적고 한 촬영 구간에 몰려 있어, 추가 자료와 다른 검출 구조의 비교가 남아 있다.

- PyTorch 후보: `outputs/lsi-training/candidate/weights/best.pt`
- ONNX 후보: `models/llvip-lsi-yolov5l-160.onnx`, 186,508,851바이트(약 186.5 MB)
- ONNX SHA-256: `bd30a221d943490a64c580e0b5f824ab5e306d5bc4cc561efd467820c724c06d`
- 원본 평가: `outputs/lsi-training/baseline-test-160/metrics.json`
- 후보 평가: `outputs/lsi-training/candidate-test-160/metrics.json`
- 기준 선정 및 적용: `outputs/lsi-training/calibration.json`, `outputs/lsi-training/candidate-test-calibrated.json`

기존 ONNX와 원본 PyTorch, 후보 ONNX와 후보 PyTorch의 출력이 각각 검증 영상 5장에서 허용 오차 이내로 일치했다. 후보는 기존 검출기에서도 로딩 및 추론을 확인했다. 이는 파일 호환성 검사이며 라즈베리파이 속도 측정은 아니다.

## 데이터 처리

- 공식 `LSIFIR.tar.gz`의 MD5는 `39b3ff8745175789304c8d47c33af904`이다. 압축 입력은 해시 확인 후 검출 자료만 해제한다.
- 검출 원본은 164x129의 단일 채널 16비트 PNG다. 주석의 `Image size` 표기는 가로와 세로가 뒤바뀌어 있으므로 실제 이미지 크기를 사용한다.
- 주석은 XML이 아닌 구형 PASCAL 텍스트다. 1부터 시작하는 포함 좌표를 YOLO 중심점/크기로 변환하고 영상 경계에 맞춘다.
- 실제 박스 주석과 명확한 음성 목록을 사용한다. 양성 목록만 있고 박스가 없는 영상이나 아무 주석도 없는 프레임을 음성으로 간주하지 않는다.
- 목록에는 누락 및 충돌이 있다. 사용하지 않은 프레임과 원본이 없는 목록 항목을 `manifest.json`의 `audit`에 기록한다. 따라서 공식 전체 프레임 수와 실제 평가 프레임 수는 다르다.
- 각 영상의 1~99 백분위값을 8비트 흑백으로 변환한다. LSI 원시값을 Lepton의 켈빈 또는 섭씨 값으로 해석하지 않는다. Lepton 프로그램의 최소 온도폭 설정과 완전히 같은 처리는 아니다.
- 공식 Train의 02 구간을 검증용으로 떼고 나머지 구간으로 학습한다. 공식 Test는 최종 비교에만 사용한다. 연속 프레임을 무작위로 섞어 학습/검증에 배분하지 않는다.
- 사람 높이 10픽셀 미만을 새로 추정하거나 라벨링하지 않는다. 제공된 검출 주석은 유지한다.

| 분할 | 영상 | 사람 박스 | 사람 없는 영상 |
| --- | ---: | ---: | ---: |
| 학습 | 3,938 | 3,371 | 1,479 |
| 검증 | 888 | 876 | 122 |
| 시험 | 8,136 | 4,169 | 4,857 |

높이 10~19픽셀인 학습 박스 91개가 모두 Train/05에 몰려 있다. 이 구간은 학습에 남기며 검증 구간에는 해당 크기 박스가 없다. 따라서 10~19픽셀의 검증 재현율은 0이 아니라 측정 불가이며, 시험 결과도 다양한 환경을 대표한다고 단정하지 않는다.

## 학습 환경

학습은 PC에서 진행한다. 확인한 환경은 Python 3.12, RTX 5070 Ti 16GB, PyTorch 2.7.1/CUDA 12.8이다. 라즈베리파이에는 학습 패키지를 설치하지 않는다.

프로젝트 루트에서 별도 가상 환경과 공식 학습 코드를 준비한다.

```powershell
python -m venv models/lsi-training-env
.\models\lsi-training-env\Scripts\Activate.ps1
python -m pip install torch==2.7.1 torchvision==0.22.1 --index-url https://download.pytorch.org/whl/cu128
python -m pip install -r training/requirements.txt
git clone --depth 1 --branch v7.0 https://github.com/ultralytics/yolov5.git models/yolov5-lsi
$env:PYTHONUTF8 = '1'
```

학습 래퍼는 YOLOv5 커밋 `915bbf294bb74c859f0b41f1c23bc395014ea679`을 확인한다. 운영 코드나 기존 LLVIP 소스는 수정하지 않는다. 체크포인트는 `weights_only` 로더에 필요한 모델 형식만 명시적으로 등록하며, 임의 피클 로딩을 허용하지 않는다. 자동 패키지 설치와 원격 실험 기록은 사용하지 않는다.

Windows에서는 검증용 글꼴을 로컬에서 준비해 첫 실행의 글꼴 다운로드를 피할 수 있다.

```powershell
New-Item -ItemType Directory -Path models/lsi-config -Force
Copy-Item C:/Windows/Fonts/arial.ttf models/lsi-config/Arial.ttf
```

## 실행 순서

공식 자료를 내려받고 변환한다. 재실행 시 기존 출력은 덮어쓰지 않으므로 새 출력 경로를 지정한다. 이미 압축을 해제했다면 `--archive` 대신 `--source data/raw/lsi-fir/extracted/LSIFIR/Detection`을 사용할 수 있다.

```powershell
New-Item -ItemType Directory -Path data/raw/lsi-fir -Force
curl.exe -fL "https://e-archivo.uc3m.es/rest/api/core/bitstreams/8157f975-838a-4de2-83e2-23e94074240f/content" -o data/raw/lsi-fir/LSIFIR.tar.gz
python training/prepare_lsi.py --archive data/raw/lsi-fir/LSIFIR.tar.gz
```

기존 `models/yolov5_trained_model/yolov5_infrared.pt`를 기준선으로 평가하고 추가 학습한다. 학습 스크립트가 확인하는 원본 SHA-256은 `79159b97fb7663bd86562d0b2cc0e377762bd0031a90dcdf1f061e45e0d860aa`이다.

```powershell
python training/evaluate_lsi.py --weights models/yolov5_trained_model/yolov5_infrared.pt --output outputs/lsi-training/baseline-val-160
python training/train_lsi.py --epochs 20 --output outputs/lsi-training/candidate
python training/evaluate_lsi.py --weights outputs/lsi-training/candidate/weights/best.pt --output outputs/lsi-training/candidate-val-160
python training/calibrate_lsi.py --evaluation outputs/lsi-training/candidate-val-160 --output outputs/lsi-training/calibration.json
```

추가 학습은 SGD, 배치 32, 초기 학습률 0.001, 시드 42를 사용한다. 최대 20회 학습하며 검증 지표가 5회 연속 개선되지 않으면 조기 종료한다. 색상/채도 변형, 모자이크, 수직 반전은 사용하지 않는다. 좌우 반전, 소폭 이동/크기 변화와 밝기 변화만 적용한다. 상세 설정은 `training/hyp-lsi.yaml`에 둔다.

학습 중 최적 가중치 선택에는 YOLOv5 기본 직사각형 검증 입력을 사용한다. 첫 실험의 별도 평가는 기본값인 160x160 고정 입력을 사용하므로, 학습 로그의 지표와 최종 비교 수치는 같지 않을 수 있다. 학습과 평가의 `--input-size`를 지정해 다른 크기를 비교할 수 있으며, 평가의 `--rect`는 패딩을 줄인다.

검증을 끝낸 후보와 원본을 같은 시험 자료로 비교한다. 시험 결과를 보고 계속 설정을 조정하면 해당 자료는 더 이상 독립적인 시험 자료가 아니다.

```powershell
python training/evaluate_lsi.py --weights models/yolov5_trained_model/yolov5_infrared.pt --split test --output outputs/lsi-training/baseline-test-160
python training/evaluate_lsi.py --weights outputs/lsi-training/candidate/weights/best.pt --split test --output outputs/lsi-training/candidate-test-160
python training/calibrate_lsi.py --evaluation outputs/lsi-training/candidate-test-160 --policy outputs/lsi-training/calibration.json --output outputs/lsi-training/candidate-test-calibrated.json
python training/export_lsi.py --weights outputs/lsi-training/candidate/weights/best.pt
```

평가는 공식 YOLOv5 AP50/AP50-95와 함께 신뢰도 0.25, NMS IoU 0.45, 일치 IoU 0.5에서 오검출/미탐 및 사람 높이별 재현율을 기록한다. 한 사람에 여러 박스가 겹쳐도 한 번만 정답으로 센다. 높이는 원본 영상 기준이다. 평가에 기록하는 GPU 배치 처리 시간은 라즈베리파이의 지연 시간이 아니다.

ONNX 변환은 고정 입력 `[1, 3, 160, 160]`, opset 13, 단일 사람 클래스를 사용한다. 형식 검사, 유한값 검사, 검증 영상 5장에 대한 PyTorch 출력 비교 및 기존 검출기 실행 검사를 통과한 경우에만 별도 모델 파일을 생성한다.

## 장비 적용

PC의 후보 모델을 라즈베리파이 프로젝트의 `models/`로 옮긴 뒤 실행한다. 원본 모델을 삭제하거나 같은 이름으로 덮어쓰지 않는다. 아래 0.75는 이번 검증 자료에서 선정한 후보 전용 기준이며, 기존 모델의 기본값을 변경하는 것은 아니다. 추가 학습을 다시 했다면 새 검증 결과의 기준을 사용한다.

```bash
cd ~/Downloads/crowd-density-analysis-system
DISPLAY=:0 .venv/bin/python sensor-client/thermal_person_detector.py \
  --device /dev/video0 \
  --model models/llvip-lsi-yolov5l-160.onnx \
  --input-size 160 \
  --confidence 0.75 \
  --inference-fps 3
```

## 남은 검증

- LSI 성능 향상은 실제 Lepton 장비 성능 향상을 보장하지 않는다. 가까운 사람을 놓치는 회귀도 함께 검사한다.
- 고온 조리기구에 의한 명암 압축, 뜨거운 아스팔트, 사람보다 뜨거운 배경과 열적 대비 부족은 해결된 것으로 간주하지 않는다.
- 실제 거리별 검출, 가림, 군중, 카메라 설치 각도, 긴 시간 동안의 오검출을 별도 평가한다.
- 기존 LLVIP 검출 능력의 유지 여부는 LLVIP 자료 또는 실제 실내 자료로 추가 확인한다. 이번 LSI 평가만으로 기존 영역의 성능 보존을 주장하지 않는다.
- 라즈베리파이에서 3 FPS 유지, CPU 사용률, 전력과 온도를 재측정한 후 운영 모델을 교체한다.
- LSI 원본의 표시 라이선스는 CC BY-NC-ND 3.0 Spain이며 LLVIP도 비상업적 연구 등의 용도로 제한된다. 현재는 로컬 연구 실험으로 한정한다. 변환 데이터나 추가 학습 가중치를 외부에 배포하거나 상업적으로 사용하기 전에는 각 권리자에게 허용 범위를 확인한다.
