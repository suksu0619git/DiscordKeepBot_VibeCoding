# EC2(Ubuntu) 새 서버에 KeepBot 올리기

새로 만든 Amazon EC2 Ubuntu 인스턴스에 처음부터 봇을 올리고, FileZilla 로 파일을
주고받고, 같은 서버(또는 다른 곳)에 웹사이트를 띄우는 것까지의 순서입니다.

최종 디렉터리 구조(운영 기준):

```
/home/ubuntu/KeepBot/
├── .env                        ← 운영 봇 토큰. git 에 올라가지 않음
├── .venv/                      ← 파이썬 가상환경
└── DiscordKeepBot_VibeCoding/  ← git clone 한 이 저장소
    ├── main.py
    ├── data/activity.db        ← 활동 DB (백업 대상)
    └── youtube_state.json      ← 유튜브 마지막 게시 상태 (백업 대상)
```

---

## 1. 인스턴스 만들기 (AWS 콘솔)

| 항목 | 값 |
|---|---|
| AMI | Ubuntu Server 24.04 LTS (64-bit x86) |
| 인스턴스 유형 | `t3.micro` (프리티어면 `t2.micro`) — 봇 전용이면 충분 |
| 키 페어 | 새로 생성 → **RSA + .pem** → 다운로드한 `keepbot.pem` 은 절대 재발급 불가하니 잘 보관 |
| 스토리지 | gp3 16GB 이상 |
| 퍼블릭 IP | 자동 할당 켜기 |

**보안 그룹(인바운드)**

| 유형 | 포트 | 소스 | 용도 |
|---|---|---|---|
| SSH | 22 | **내 IP** | 접속·FileZilla |
| HTTP | 80 | 0.0.0.0/0 | 웹사이트 (안 쓸 거면 열지 말 것) |
| HTTPS | 443 | 0.0.0.0/0 | 웹사이트 |

봇은 아웃바운드 연결만 쓰므로 **인바운드에 따로 열 포트가 없습니다.**

> **탄력적 IP(Elastic IP)**: 인스턴스를 중지/시작하면 퍼블릭 IP가 바뀝니다.
> 웹사이트에 도메인을 연결할 거라면 EC2 → 탄력적 IP → 할당 → 인스턴스에 연결까지 해두세요.
> (인스턴스에 연결된 상태면 무료, 놀리면 과금됩니다.)

## 2. SSH 접속

Windows PowerShell 에서 바로 됩니다.

```powershell
# .pem 권한 정리(본인만 읽기). 이걸 안 하면 SSH 가 키를 거부합니다.
icacls .\keepbot.pem /inheritance:r
icacls .\keepbot.pem /grant:r "$env:USERNAME:(R)"

ssh -i .\keepbot.pem ubuntu@<퍼블릭IP>
```

## 3. 서버 기본 세팅

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3-venv python3-pip git

# 한국 시간대로 (로그 시각이 KST 로 찍혀 확인이 편합니다)
sudo timedatectl set-timezone Asia/Seoul

# t2/t3.micro(1GB) 는 pip 설치 중 메모리가 모자랄 수 있으니 스왑 2GB
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

## 4. 코드 내려받기 + 가상환경

```bash
mkdir -p ~/KeepBot && cd ~/KeepBot
git clone <저장소 URL> DiscordKeepBot_VibeCoding

python3 -m venv .venv
./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install -r DiscordKeepBot_VibeCoding/requirements.txt
```

비공개 저장소면 GitHub 에서 **배포 키(Deploy key)** 를 쓰는 게 안전합니다.

```bash
ssh-keygen -t ed25519 -C "keepbot-ec2" -f ~/.ssh/id_ed25519 -N ""
cat ~/.ssh/id_ed25519.pub    # 출력값을 GitHub 저장소 → Settings → Deploy keys 에 등록
```

## 5. `.env` 옮기기

설정 파일은 `.env` **하나뿐**이고 git 에 올라가지 않으므로, 로컬에 있는 걸 직접
복사해야 합니다. 두는 위치는 **저장소 안이 아니라 상위 폴더(`~/KeepBot/.env`)** 입니다.
`config.py` 가 ① `KEEPBOT_ENV_FILE` → ② 봇 폴더 → ③ 상위 폴더 순으로 찾기 때문에
여기 두면 `git pull` 로 덮일 일이 없습니다.

로컬 PowerShell 에서 한 줄로 올릴 수 있습니다(FileZilla 로 올려도 됩니다):

```powershell
scp -i .\keepbot.pem .\DiscordKeepBot_VibeCoding\.env ubuntu@<퍼블릭IP>:/home/ubuntu/KeepBot/.env
```

```bash
chmod 600 ~/KeepBot/.env      # 토큰 파일이므로 권한을 조입니다
nano ~/KeepBot/.env           # 서버용 채널/역할 ID 확인
```

키 목록과 기본값은 저장소 README 의 '설정(.env)' 표에 있습니다. 확인할 값:

```ini
TOKEN=봇토큰
ACTIVITY_PERIOD_DAYS=90     # 만료일 = 마지막 활동일 + 90일
ACTIVITY_ORANGE_DAYS=60     # 활동 +60일 초과 → 🟠 + "OOO 님이 +61일째입니다!" 공지
ACTIVITY_RED_DAYS=90        # 활동 +90일 초과 → 🔴
ACTIVITY_NOTIFY_CHANNEL_ID=활동공지채널ID
```

한 번 손으로 띄워서 로그를 확인합니다.

```bash
cd ~/KeepBot/DiscordKeepBot_VibeCoding
../.venv/bin/python -u main.py      # 첫 줄에 "설정 파일: /home/ubuntu/KeepBot/.env"
# Ctrl+C 로 종료
```

> 로컬 PC 와 서버가 같은 봇 토큰을 쓰므로 **둘을 동시에 켜면 명령어가 두 번 반응합니다.**
> 로컬에서 테스트할 땐 `sudo systemctl stop discordbot` 으로 서버를 멈춰두세요.

## 6. systemd 등록 (자동 시작 · 자동 재기동)

```bash
sudo cp ~/KeepBot/DiscordKeepBot_VibeCoding/deploy/discordbot.service /etc/systemd/system/
sudo nano /etc/systemd/system/discordbot.service   # 경로가 위 구조와 같으면 수정할 것 없음
sudo systemctl daemon-reload
sudo systemctl enable --now discordbot
```

| 하고 싶은 일 | 명령 |
|---|---|
| 상태 확인 | `systemctl status discordbot` |
| 실시간 로그 | `journalctl -u discordbot -f` |
| 오늘 로그만 | `journalctl -u discordbot --since today` |
| 코드 갱신 후 재시작 | `cd ~/KeepBot/DiscordKeepBot_VibeCoding && git pull && sudo systemctl restart discordbot` |

디스코드 `/재시작` 명령은 종료 코드 1로 빠져나오고 `Restart=on-failure` 가 다시 띄우는 구조입니다.

## 7. 백업

DB 와 유튜브 상태 파일만 챙기면 됩니다.

```bash
crontab -e
# 매일 새벽 4시, 최근 14일치 보관
0 4 * * * cd /home/ubuntu/KeepBot/DiscordKeepBot_VibeCoding && tar czf /home/ubuntu/backup-$(date +\%F).tgz data youtube_state.json && find /home/ubuntu -name 'backup-*.tgz' -mtime +14 -delete
```

---

## FileZilla 로 EC2 에 접속하기 (SFTP)

**FTP 서버를 따로 설치하지 않습니다.** EC2 는 SSH(22번)가 열려 있으면 그 위에서
SFTP 가 그대로 동작합니다. FileZilla 에서 프로토콜만 SFTP 로 고르면 됩니다.

### 준비물 두 가지

| 필요한 것 | 어디서 확인 |
|---|---|
| 퍼블릭 IP | AWS 콘솔 → EC2 → 인스턴스 → 해당 인스턴스 클릭 → **퍼블릭 IPv4 주소** |
| `.pem` 키 파일 | 인스턴스 만들 때 다운로드한 `keepbot.pem` (재발급 불가) |

> 퍼블릭 IP 는 인스턴스를 중지했다 켜면 **바뀝니다.** 매번 다시 입력하기 싫으면
> 위 1단계의 탄력적 IP 를 먼저 붙여두세요.

### 연결 단계

1. FileZilla 실행 → 상단 메뉴 `파일` → **사이트 관리자**(`Ctrl+S`) → 왼쪽 아래 **`새 사이트`** → 이름을 `KeepBot EC2` 등으로
2. 오른쪽 `일반` 탭을 이렇게 채웁니다.

   | 항목 | 값 |
   |---|---|
   | 프로토콜 | **SFTP - SSH File Transfer Protocol** ← FTP 가 아닙니다 |
   | 호스트 | EC2 퍼블릭 IP (예: `13.125.xxx.xxx`) |
   | 포트 | `22` |
   | 로그온 유형 | **키 파일** |
   | 사용자 | `ubuntu` ← Ubuntu AMI 의 기본 계정. `root`/`ec2-user` 아님 |
   | 키 파일 | `찾아보기` → 파일 형식을 **모든 파일**로 바꾼 뒤 `keepbot.pem` 선택 |

3. 키를 고르면 *"PuTTY 형식(.ppk)으로 변환할까요?"* 창이 뜹니다 → **예** →
   변환본을 `keepbot.ppk` 로 저장(원본 옆에 두면 됩니다). FileZilla 는 이 `.ppk` 를 씁니다.
4. **`연결`** 클릭 → 처음 한 번 *"알 수 없는 호스트 키"* 경고 → **`이 호스트를 항상 신뢰`** 체크 후 `확인`
5. 접속되면 창이 좌우로 나뉩니다. **왼쪽 = 내 PC, 오른쪽 = 서버**.
   오른쪽에 `/home/ubuntu` 내용이 보이면 성공입니다.

`고급` 탭의 **기본 원격 디렉터리**에 `/home/ubuntu/KeepBot` 을 적어두면 접속할 때마다
그 폴더에서 시작합니다.

### 파일 올리는 법

왼쪽(내 PC)에서 파일을 오른쪽(서버) 창으로 **드래그**하면 업로드, 반대로 끌면 다운로드입니다.
(오른쪽 클릭 → `업로드` / `다운로드` 도 동일)

| 올릴 것 | 서버 경로 |
|---|---|
| `.env` | `/home/ubuntu/KeepBot/.env` — 올린 뒤 SSH 에서 `chmod 600` |
| 백업해둔 `activity.db` | `/home/ubuntu/KeepBot/DiscordKeepBot_VibeCoding/data/` |
| `youtube_state.json` | `/home/ubuntu/KeepBot/DiscordKeepBot_VibeCoding/` |

### 잘 안 될 때

| 증상 | 원인과 해결 |
|---|---|
| `Connection timed out` | 보안 그룹 인바운드에 SSH 22가 없거나, 소스가 '내 IP'인데 공인 IP가 바뀜 → 규칙 편집에서 다시 '내 IP' 선택 |
| `Connection refused` | 인스턴스가 중지 상태이거나 IP를 잘못 적음 (중지 후 재시작하면 IP가 바뀝니다) |
| `Too many authentication failures` / 로그인 거부 | 사용자명이 `ubuntu` 가 맞는지, 키 파일이 그 인스턴스의 키가 맞는지 확인 |
| 키 파일을 못 고름 | 파일 선택창의 형식이 `.ppk` 로 고정돼 있음 → **모든 파일**로 변경 |
| 접속은 되는데 업로드가 `Permission denied` | `/home/ubuntu` 밖(예: `/var/www`)에 쓰려는 경우. SSH 로 `sudo chown -R ubuntu:ubuntu <경로>` 하거나 홈 폴더에 올린 뒤 `sudo mv` |

### 초기화 / 다시 잡기

- 사이트 설정만 지우기: `파일` → 사이트 관리자에서 해당 사이트 선택 → `삭제` 후 위 순서로 재등록
- 호스트 키 경고가 계속 뜰 때: `편집` → `설정` → `SFTP` 에서 등록된 키 제거
- 완전 초기화: FileZilla 종료 후 `%APPDATA%\FileZilla` 의 `sitemanager.xml`,
  `filezilla.xml` 삭제 (설정이 전부 지워지므로 사이트를 다시 등록해야 합니다)

주의할 점 두 가지:

- **`.env` 는 FileZilla 로 올리지 말고 서버에서 직접 편집**하는 편이 안전합니다.
  올릴 거라면 업로드 후 `chmod 600 ~/KeepBot/.env` 를 다시 실행하세요.
- 코드는 FileZilla 대신 **`git pull`** 로 갱신하세요. 손으로 덮어쓰면 서버와 저장소
  내용이 달라져서 나중에 무엇이 배포된 건지 알 수 없게 됩니다.
- FileZilla 는 기본이 **ASCII/자동 전송 모드**입니다. `data/activity.db` 같은 파일을
  받을 땐 `전송` → `전송 형식` → **바이너리**로 두세요(자동 모드에서 깨질 수 있음).

---

## 웹사이트는 어디에?

용도에 따라 두 갈래입니다.

### A. 소개/포트폴리오 같은 정적 사이트 → EC2 말고 무료 호스팅 권장

Cloudflare Pages / Vercel / Netlify / GitHub Pages. GitHub 저장소를 연결하면
푸시할 때마다 자동 배포되고, HTTPS 인증서도 자동입니다. 서버 관리가 0이라
EC2 에 얹는 것보다 훨씬 편하고, 봇이 도는 서버의 자원·보안 위험도 건드리지 않습니다.
도메인만 사서(가비아/Namecheap/Cloudflare) DNS 를 연결하면 끝입니다.

### B. 봇 DB를 읽는 관리 페이지 등 서버가 필요한 사이트 → 같은 EC2 에 Nginx

```bash
sudo apt install -y nginx
sudo ufw allow 'Nginx Full' && sudo ufw allow OpenSSH && sudo ufw enable

# 정적 파일만 올릴 경우: /var/www/keepbot 에 index.html 을 두고
sudo mkdir -p /var/www/keepbot
```

`/etc/nginx/sites-available/keepbot`:

```nginx
server {
    listen 80;
    server_name example.com www.example.com;

    root /var/www/keepbot;
    index index.html;

    # 파이썬 웹앱(FastAPI/Flask)을 127.0.0.1:8000 에 띄운 경우에만 사용
    # location /api/ {
    #     proxy_pass http://127.0.0.1:8000/;
    #     proxy_set_header Host $host;
    #     proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    # }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/keepbot /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
```

**도메인 연결 → HTTPS**

1. 도메인 구입(가비아 등) 또는 Route 53 에서 등록
2. DNS 에 A 레코드: `example.com` → 탄력적 IP, `www` → 같은 IP
3. 인증서(무료, 자동 갱신):

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d example.com -d www.example.com
```

웹앱을 파이썬으로 만든다면 봇과 **별도의 systemd 서비스**로 띄우세요
(`keepweb.service`). 한 프로세스에 섞으면 웹 오류로 봇까지 죽습니다.
DB(`data/activity.db`)를 웹에서도 읽는다면 읽기 전용(`file:...?mode=ro`)으로 여는 것이 안전합니다.
