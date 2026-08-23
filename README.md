# KeepBot

Pycord 기반 디스코드 봇. 영상 크레딧 생성, 멤버 활동 기간 관리, 유튜브 업로드 포럼 게시,
익명 채팅, 반응 포워딩 기능을 제공합니다.

## 요구 사항

- Python 3.10 이상 (개발/검증 환경: 3.13)
- 1py-cord 2.8 이상 (**discord.py 아님**)

## 설정 (.env)

설정 파일은 **`.env` 하나뿐**입니다(`.gitignore` 대상이라 커밋되지 않습니다).
`config.py` 가 **① `KEEPBOT_ENV_FILE` → ② 봇 폴더/`.env` → ③ 상위 폴더/`.env`**
순으로 찾아 먼저 찾은 하나만 읽고, 기동 로그 첫 줄에 실제로 읽은 경로가 찍힙니다.
EC2 에서는 저장소 바깥(`~/KeepBot/.env`)에 두면 `git pull` 로 덮일 일이 없습니다.

`.env` 에 없는 키는 아래 기본값으로 동작하므로, **기본값 그대로 쓸 키는 적지 않아도 됩니다.**

| 키 | 기본값 | 설명 |
|---|---|---|
| `TOKEN` | (필수) | 봇 토큰. 없으면 기동하지 않습니다 |
| `LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `AUTO_SYNC_COMMANDS` | `true` | 접속 시 슬래시 명령어 동기화 |
| `DEV_USER_IDS` | — | `/리로드` · `/재시작` 을 쓸 user_id (쉼표 구분) |
| `ACTIVITY_ADMIN_ROLE_ID` / `..._NAME` | — | `/활동*` 명령어를 쓸 역할 (ID 우선) |
| `ACTIVITY_NOTIFY_CHANNEL_ID` | — | 만료 임박 공지를 보낼 채널 |
| `ACTIVITY_PERIOD_DAYS` | `90` | 만료일 = 마지막 활동일 + 이 일수 |
| `EXPIRATION_WARN_DAYS` | `21` | 며칠 남았을 때 공지할지 |
| `ACTIVITY_DB_PATH` | `data/activity.db` | 활동 DB 경로 |
| `YOUTUBE_API_KEY` / `YOUTUBE_CHANNEL_ID` | — | 업로드 감지 (`UC...` 채널 ID) |
| `YOUTUBE_CHECK_INTERVAL_MINUTES` | `5` | 업로드 확인 주기(분) |
| `FORUM_CHANNEL_ID` | — | 새 영상을 포스트로 만들 포럼 채널 |
| `YOUTUBE_LONGFORM_TAG_NAME` / `..._SHORTS_...` | `롱폼` / `쇼츠` | 포럼 태그 이름 또는 태그 ID |
| `YOUTUBE_STATE_PATH` | `youtube_state.json` | 마지막 게시 영상 기록 |
| `ANONYMOUS_CHANNEL_ID` | — | 익명 채널 |
| `MEETING_NOTIFY_CHANNEL_ID` | — | `/일정` 알림 채널 |
| `TARGET_CHANNEL_ID` | — | ⭐ 반응 포워딩 대상 채널 |
| `SOURCE_CHANNEL_IDS` | (전체) | 반응을 감지할 채널들 (쉼표 구분) |
| `FORWARDED_DB_PATH` | `forwarded.json` | 포워딩 중복 방지 기록 |
| `REACTION_PIN_EMOJI` / `REACTION_MIN_COUNT` / `REACTION_SUPER_COUNT` | `⭐` / `5` / `30` | 포워딩 트리거 |

## 로컬 실행

```bash
cd DiscordKeepBot_VibeCoding
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

로컬과 서버가 **같은 봇 애플리케이션**을 씁니다. 둘을 동시에 켜두면 명령어가 두 번
반응하므로, 로컬에서 돌릴 때는 서버 쪽을 멈춰두세요(`sudo systemctl stop discordbot`).

## 테스트

디스코드 연결 없이 전부 실행됩니다.

```bash
cd DiscordKeepBot_VibeCoding
python -m unittest discover -s tests -v
```

## 구조

```
main.py                    진입점 (로깅/인텐트 설정, cogs 로딩, 재시작 종료 코드 처리)
config.py                  모든 설정값의 단일 진입점 (.env 로더)
cogs/
  credit.py                /크레딧 — 2단계 모달로 영상 크레딧 생성 (3D 등 일부 항목은 선택)
  activity.py              /활동* — 멤버 활동 기간(마지막 활동 +90일) 관리 + 만료 임박 채널 공지
  admin.py                 /리로드 · /재시작 (개발자 전용)
  youtube.py               유튜브 업로드 감지 → 포럼 포스트 자동 생성
  anonymous.py             익명 채널 처리
  emoje.py                 커스텀 이모지 확대 (익명 채널 제외)
  reaction_forward.py      ⭐ 반응 포워딩 (포럼 스레드 포함)
  event_listener.py        /일정 — 일정 예약 알림
services/                  Discord 의존성이 없는 공용 로직 (단위 테스트 대상)
  activity_db.py           aiosqlite DAO + 날짜 연산
  credit_format.py         크레딧 텍스트 포맷터
  youtube_utils.py         유튜브 URL/제목 유틸
tests/                     단위 · 통합 테스트
deploy/discordbot.service  systemd unit 예시
```

### /크레딧 선택 항목

영상에 따라 없을 수 있는 항목은 **비워둔 채 제출**할 수 있고, 비어 있으면 출력에서
해당 줄이 통째로 빠집니다(빈 `3D : ` 줄을 남기지 않습니다).

| 항목 | 필수 여부 |
|---|---|
| 제목 · Production · Edit · World · 태그 | 필수 |
| 3D · Filming · Act | 선택 — 아무도 안 고르면 그 줄이 빠짐 |
| 음악 · 음악 링크 | 선택 — 둘 다 비면 `MUSIC` 단락 전체가 빠짐 |

필수/선택 구분은 `services/credit_format.py` 의 `OPTIONAL_LABELS` 와
`CreditData._REQUIRED_LABELS` 한 곳에서만 관리합니다.

## 봇 권한

| 기능           | 필요한 권한                                |
| -------------- | ------------------------------------------ |
| 익명 채팅      | 메시지 관리, 웹훅 관리                     |
| 이모지 확대    | 메시지 관리, 웹훅 관리                     |
| 포럼 자동 게시 | 스레드 만들기(공개), 메시지 보내기         |
| 반응 포워딩    | 메시지 기록 보기, 메시지 보내기, 파일 첨부 |

인텐트는 개발자 포털에서 **Message Content Intent** 와 **Server Members Intent** 를 켜야 합니다.

## EC2 배포 (systemd)

> 서버를 **새로 만드는 경우**(인스턴스 생성 · 보안 그룹 · FileZilla · 웹사이트 호스팅까지)는
> [deploy/EC2_SETUP.md](deploy/EC2_SETUP.md) 를 순서대로 따라가세요. 아래는 요약본입니다.

### 최초 1회 등록

```bash
# 1) 코드와 가상환경 준비
cd /home/ubuntu/KeepBot
git pull
python3 -m venv .venv
./.venv/bin/pip install -r DiscordKeepBot_VibeCoding/requirements.txt

# 2) .env 준비 — 로컬의 .env 를 서버로 복사하거나 직접 작성 (위 설정 표 참고)
nano .env
chmod 600 .env

# 3) systemd 등록
sudo cp DiscordKeepBot_VibeCoding/deploy/discordbot.service /etc/systemd/system/
sudo nano /etc/systemd/system/discordbot.service   # User / WorkingDirectory / ExecStart 경로 확인
sudo systemctl daemon-reload
sudo systemctl enable --now discordbot

# 4) 확인
systemctl status discordbot
journalctl -u discordbot -f
```

### 이후 배포

```bash
cd /home/ubuntu/KeepBot
git pull
./.venv/bin/pip install -r DiscordKeepBot_VibeCoding/requirements.txt   # 의존성 변경 시에만
sudo systemctl restart discordbot
journalctl -u discordbot -n 50 --no-pager
```

Cog 코드만 고쳤다면 재시작 없이 디스코드에서 `/리로드 <cog명>` 으로 반영할 수 있습니다.
(`config.py` 나 `services/` 를 고친 경우에는 `/재시작` 또는 `systemctl restart` 가 필요합니다.)

### 재시작 동작

`/재시작` 은 `bot.close()` 후 **종료 코드 1** 로 프로세스를 끝냅니다.
systemd 의 `Restart=on-failure` 가 이를 감지해 자동으로 재기동합니다.
(종료 코드가 0이면 systemd 가 재기동하지 않으므로 의도적으로 1을 반환합니다.)
