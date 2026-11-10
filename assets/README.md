# 디자인 에셋

Google Flow로 만든 이미지와 영상을 넣는 곳. 프롬프트는 `docs/01_기획서.md` 6장 '디자인 에셋 제작'.

## 폴더

테마별로 나눈다: `assets/plain/` (단정한 모니터), `assets/pink/` (핑크 오퍼레이터). 두 폴더의 파일 이름은 같게 맞춘다. 장식, 반짝임 영상, 시작 화면은 `pink/`에만 있다.

## 이름 규칙

| 파일 | 내용 |
|---|---|
| `bezel@2x.png` | 1번 CRT 베젤 (화면 자리는 투명) |
| `btn-primary@2x.png` | 2번 주 버튼 기본 |
| `btn-primary-hover@2x.png` | 2번 마우스 올림 |
| `btn-primary-pressed@2x.png` | 2번 누름 |
| `btn-secondary@2x.png` | 3번 보조 버튼 |
| `band-texture.png` | 4번 관제 띠 질감 (이음매 없는 반복) |
| `gems@2x.png` | 5번 보석 점 6색 |
| `pearl-strand@2x.png` | 6번 진주 장식 띠 |
| `sparkle.webp` | 7번 반짝임 (움직이는 WebP 또는 GIF) |
| `splash@2x.png` | 8번 시작 화면 |

## 만든 것 (2026-11-07, Google Flow Nano Banana 2.1)

| 파일 | plain | pink | 비고 |
|---|---|---|---|
| `bezel@2x.png` | 있음 | 있음 | plain을 먼저 만들고 참고 이미지로 넣어 재질만 바꿨다. 화면 자리 위치 차이 3px 안 |
| `btn-primary@2x.png` | 남색 | 로즈 | |
| `btn-primary-hover@2x.png`, `-pressed@2x.png` | 있음 | 있음 | Flow로 다시 뽑지 않고 기본 모양을 밝게(1.05), 어둡게(0.82) 손질. 9분할 기준선이 같게. 1.15배는 흰 글씨 대비가 모자랐다 |
| `gem-1@2x.png` ~ `gem-6@2x.png` | | 있음 | `gems@2x.png`를 하나씩 나눈 것. 유형 표시에 쓴다(theme.json `assets.gems`) |
| `btn-secondary@2x.png` | 은색 고리 | 로즈골드 고리 | 가운데는 투명 |
| `band-texture.png` | 있음 | 있음 | 평균 색 = 테마 `band` 토큰, 명암은 아주 약하게. plain은 Flow 회색 질감이 정책 검사에 세 번 걸려 베젤 몸체의 결을 썼다 |
| `gems@2x.png` | | 있음 | 6색 한 줄. 민트가 지워지지 않게 초록 기준을 올렸다 |
| `pearl-strand@2x.png` | | 있음 | |
| `crown@2x.png`, `heart@2x.png` | | 있음 | 한 장에 둘을 만들어 나눴다 |
| `splash@2x.png` | | 있음 | 워터마크가 든 아래쪽을 잘라 16:9 |
| `sparkle.webp` | | 있음 | Veo 3.1 Lite(2개 20크레딧) 중 터졌다 사라지기를 되풀이하는 쪽의 첫 주기 4초. 480×270, 12장/초, 1.4MB. 투명도는 밝기로 정했다(초록 정도로 정하면 영상 압축 탓에 알갱이마다 어두운 테두리가 남는다) |

- 베젤의 전원 표시등은 그림에서 지웠다(9분할로 늘리면 퍼진다). 코드로 그리거나 `heart@2x.png`를 얹는다.
- 다시 만들기: `python tools/assets.py all` (`assets/_source/`의 원본에서 이 폴더의 파일을 모두 다시 만든다)
- Flow 무료 생성본에는 오른쪽 아래 구석에 반짝임 표시(워터마크)가 들어간다. 손질 도구가 지운다. 물건이 그 구석에 걸린 결과는 쓰지 않았다.

## 넣기 전에

- 초록 배경(`#00FF00`)을 지워 투명 PNG로 만든다.
- 이미지 안에 글자가 들어가 있으면 다시 만든다.
- 원본 생성 파일은 `assets/_source/`에 따로 둔다.
