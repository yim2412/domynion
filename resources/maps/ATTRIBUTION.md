# 지도 리소스 출처

이 디렉터리의 `*.bin` 과 `manifest.json` 은 **OpenFront** 프로젝트의 리소스를
그대로 가져온 것이다.

- 출처: https://github.com/openfrontio/OpenFrontIO — `resources/maps/`
- 리소스 라이선스: **Creative Commons Attribution-ShareAlike 4.0 International**
  (CC BY-SA 4.0) — https://creativecommons.org/licenses/by-sa/4.0/
- OpenFront 코드 라이선스: **AGPL-3.0**

Domynion 은 OpenFront 의 게임 공식을 이식하고 있으므로, **배포할 경우 AGPL-3.0 과
CC BY-SA 4.0 의 의무를 따른다**(소스 공개·동일조건변경허락·출처 표시).

## 포맷

1 바이트 / 타일, 행 우선(row-major), 크기는 `manifest.json` 의 `map16x`.

```
bit 7      육지 여부
bit 6      해안선
bit 5      대양
bits 0-4   magnitude (0~30, 31 = 통행 불가)

지형: magnitude < 10 → Plains,  < 20 → Highland,  그 외 → Mountain
```
