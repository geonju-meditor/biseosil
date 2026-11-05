# 비서실 (Biseosil)

팀 단톡·업무 메일·일정에 연락이 흩어진 직장인을 위한 AI 비서 팀입니다.
카카오톡 대화 내보내기, 메일, 캘린더(.ics), 자유 메모를 넣으면 팀장과 전문 비서들이 나눠 읽고
내 할 일, 일정 제안, 답장할 것을 **원문 근거와 함께** 정리합니다.

- 배포 앱: https://ca-biseosil-dev-app.ambitiousplant-e1c78535.koreacentral.azurecontainerapps.io
- 로그인 없이 **샘플로 바로 해보기** → 약 40초 → 결과 탭에서 근거 보기 → 검토 탭에서 비서끼리 조정 대화 확인
- 제품 요구사항: [PRD.md](PRD.md) · 기술 설계: [TRD.md](TRD.md)

## 구조

```
입력(카톡 · 메일 · 캘린더 · 메모) → 채널별 수집 비서(동시) → 팀장
  → 할 일 · 일정 · 답장 비서(동시) → 검토 비서 ↔ 일정 비서(충돌 조정 대화) → 사용자 확인
```

- GitHub Copilot SDK + Microsoft Agent Framework(`GitHubCopilotAgent`, `WorkflowBuilder` fan-out/fan-in)
- 진행 상황은 AG-UI 이벤트(SSE)로 실시간 표시
- Azure Container Apps, Key Vault(관리 ID), Application Insights, `azd` + GitHub Actions 배포

## 로컬 실행

```sh
cd src/backend
uv sync
COPILOT_GITHUB_TOKEN=$(gh auth token) uv run uvicorn app.main:app --port 8000
uv run pytest
```

Copilot을 쓸 수 없으면 `AGENT_PROVIDER=deterministic`으로 규칙 기반 대체 비서가 동작합니다.
도구 설치와 토큰 준비는 [SETUP.md](SETUP.md)를 보세요.
