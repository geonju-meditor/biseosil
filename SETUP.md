# SETUP.md — 설치·준비 순서 (macOS 기준)

> 이 맥에서 확인한 상태(11/5 11:40): brew, gh, node는 있음.
> **없음: VS Code(`code`), Azure CLI(`az`), azd, uv, Python 3.12, Docker, Copilot CLI.**

## 1. 설치 (터미널에 그대로 붙여넣기)

```sh
# VS Code
brew install --cask visual-studio-code

# Azure CLI + Azure Developer CLI(azd)
brew install azure-cli
brew tap azure/azd && brew install azd

# Python 3.12 + uv (패키지 관리)
brew install python@3.12 uv

# GitHub Copilot CLI (로컬에서 Copilot SDK가 로그인 정보를 쓰기 위함)
npm install -g @github/copilot
```

- Docker는 **설치하지 않아도 됩니다.** `azure.yaml`에서 `remoteBuild: true`로 두면
  이미지가 Azure 쪽(ACR)에서 빌드됩니다. Copilot에게 그렇게 만들어 달라고 하세요.
- 설치 확인: `code --version && az version && azd version && uv --version && copilot --version`

## 2. VS Code 확장

- **GitHub Copilot Chat** (대회 지정 도구) — 설치 후 GitHub 계정 로그인, Agent 모드 사용
- Python, Azure Container Apps(선택)

## 3. 로그인

```sh
gh auth login            # 이미 되어 있으면 생략 (gh auth status로 확인)
copilot                  # 처음 실행 시 /login 으로 GitHub 로그인 → 종료
az login
azd auth login
```

- Azure 구독이 여러 개면: `az account set --subscription "<구독 이름>"`
- Azure 비용 정산 상한은 팀당 5만 원. 사용량 캡처·영수증을 보관하세요.

## 4. 배포용 GitHub 토큰 (Azure에 올린 앱이 Copilot을 부를 때 필요)

- Copilot 라이선스가 있는 계정에서 **fine-grained PAT**를 만들고
  **"Copilot Requests" 권한**을 줍니다. (정확한 절차는 copilot-sdk 리포의 `docs/auth/` 확인)
- 로컬: `src/backend/.env`에 `COPILOT_GITHUB_TOKEN=...` (커밋 금지)
- Azure: Key Vault에 넣고 Container App이 관리 ID로 읽게 합니다. (`azd up` 정의에 포함)
- **토큰을 채팅·이슈·커밋·스크린샷에 노출하지 마세요.** 노출되면 바로 재발급.

## 5. 리포 연결

```sh
cd ~/biseosil
git init && git add . && git commit -m "docs: AGENTS, IDEATION, SETUP"
gh repo create biseosil --public --source . --push   # 팀장 개인 계정으로
code .
```

## 6. 그다음

VS Code Copilot Chat(Agent 모드)에서:

> AGENTS.md, IDEATION.md, SETUP.md를 읽고, IDEATION의 "아직 미정" 항목을 하나씩 나에게 물어봐. 정해지면 문서를 갱신해.
