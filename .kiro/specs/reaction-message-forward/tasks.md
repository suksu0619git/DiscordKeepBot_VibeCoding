# 구현 계획: 반응 기반 메시지 포워딩 기능

## 개요

이 문서는 Discord 봇에 📌 이모지 반응 감지 시 메시지를 지정된 채널로 자동 포워딩하는 기능을 구현하기 위한 작업 계획을 정의합니다. 모든 작업은 Python과 py-cord 라이브러리를 사용하여 구현됩니다.

## 작업 목록

- [ ] 1. 프로젝트 구조 및 기본 Cog 설정
  - [ ] 1.1 ReactionForward Cog 파일 생성 및 기본 구조 구현
    - `cogs/reaction_forward.py` 파일 생성
    - `ReactionForward` 클래스 정의 (commands.Cog 상속)
    - `__init__` 메서드 구현 (bot, target_channel_id 파라미터)
    - `setup` 함수 정의
    - 기본 상수 정의 (PIN_EMOJI, FORWARDED_DB_FILE, EMBED_COLOR)
    - _요구사항: 7.1, 7.2, 7.3, 4.1, 4.2, 4.3_

  - [ ] 1.2 main.py에 필수 Intent 추가
    - `main.py`에 `intents.guild_reactions = True` 추가
    - 봇 재시작 후 정상 로딩 확인
    - _요구사항: 1.2_

- [ ] 2. 중복 추적 메커니즘 구현
  - [ ] 2.1 JSON 파일 로드/저장 메서드 구현
    - `_load_forwarded_ids()` 메서드 구현
    - `_save_forwarded_ids()` 메서드 구현
    - JSON 파일 오류 처리 (파일 없음, 손상, 권한 오류)
    - 초기화 시 자동 로드 통합
    - _요구사항: 5.1, 5.5, 6.1_

  - [ ] 2.2 중복 확인 및 마킹 메서드 구현
    - `_is_forwarded(message_id)` 메서드 구현
    - `_mark_forwarded(message_id)` 메서드 구현
    - Set 자료구조를 사용한 O(1) 조회
    - _요구사항: 5.2, 5.3, 5.4_

- [ ] 3. 반응 이벤트 감지 및 필터링
  - [ ] 3.1 on_raw_reaction_add 이벤트 리스너 구현
    - `@commands.Cog.listener()` 데코레이터 추가
    - `on_raw_reaction_add(payload)` 메서드 구현
    - 봇 자신의 반응 필터링 (payload.user_id == bot.user.id)
    - 📌 이모지 필터링 (str(payload.emoji) != "📌")
    - 중복 확인 통합 (\_is_forwarded 호출)
    - _요구사항: 1.1, 1.2, 1.3, 5.2, 5.3_

- [ ] 4. 메시지 정보 검색 및 포워딩 로직
  - [ ] 4.1 원본 메시지 가져오기 메서드 구현
    - `_fetch_original_message(payload)` 메서드 구현
    - 채널 조회 (get_channel, fetch_channel)
    - 메시지 조회 (fetch_message)
    - 오류 처리 (NotFound, Forbidden, HTTPException)
    - _요구사항: 2.1, 6.2_

  - [ ] 4.2 대상 채널 가져오기 메서드 구현
    - `_get_target_channel()` 메서드 구현
    - 채널 ID로 채널 객체 조회
    - 오류 처리 (채널 없음, 권한 없음)
    - _요구사항: 3.6, 6.1_

  - [-] 4.3 임베드 생성 메서드 구현
    - `_create_forward_embed(message)` 메서드 구현
    - 작성자 정보 추가 (set_author)
    - 메시지 콘텐츠 추가 (description)
    - 첫 번째 이미지 첨부 파일 추가 (set_image)
    - 원본 메시지 링크 필드 추가 (add_field)
    - 색상 및 타임스탬프 설정
    - 빈 콘텐츠 처리 ("_첨부 파일만 포함_")
    - _요구사항: 2.2, 2.3, 2.4, 2.5, 3.2, 3.3, 3.4, 3.5_

  - [ ] 4.4 안전한 메시지 포워딩 메서드 구현
    - `_forward_message_safe(payload)` 메서드 구현
    - 대상 채널 가져오기
    - 원본 메시지 가져오기
    - 임베드 생성
    - 메시지 전송 (target_channel.send(embed=embed))
    - 성공 시 중복 추적 표시 (\_mark_forwarded)
    - 포괄적 오류 처리 (Forbidden, NotFound, HTTPException)
    - _요구사항: 3.1, 5.4, 6.1, 6.2, 6.3, 6.4_

- [ ] 5. 체크포인트 - 기본 기능 테스트
  - 봇 실행 및 로딩 확인
  - 📌 반응 추가 시 포워딩 테스트
  - 중복 방지 테스트 (동일 메시지에 다시 반응)
  - 오래된 메시지에 반응 테스트
  - 다른 이모지 반응 테스트 (무시되는지 확인)
  - 모든 테스트 통과 시 사용자에게 확인 요청

- [ ]\* 6. 단위 테스트 작성
  - [ ]\* 6.1 중복 추적 테스트
    - `test_is_forwarded_returns_true_for_existing_id` 작성
    - `test_is_forwarded_returns_false_for_new_id` 작성
    - `test_mark_forwarded_adds_id_to_set` 작성
    - `test_load_forwarded_ids_handles_missing_file` 작성
    - _테스트 대상: \_is_forwarded, \_mark_forwarded, \_load_forwarded_ids_

  - [ ]\* 6.2 임베드 생성 테스트
    - `test_create_embed_includes_author_info` 작성
    - `test_create_embed_handles_empty_content` 작성
    - `test_create_embed_includes_first_image_only` 작성
    - _테스트 대상: \_create_forward_embed_

  - [ ]\* 6.3 이벤트 핸들러 통합 테스트
    - `test_on_reaction_add_forwards_new_message` 작성
    - `test_on_reaction_add_ignores_duplicate` 작성
    - `test_on_reaction_add_ignores_non_pin_emoji` 작성
    - `test_forward_handles_channel_not_found` 작성
    - unittest.mock을 사용한 모의 객체 생성
    - _테스트 대상: on_raw_reaction_add, 전체 포워딩 흐름_

- [ ] 7. 문서화 및 배포 준비
  - [ ] 7.1 코드 주석 및 docstring 추가
    - 모든 메서드에 docstring 추가
    - 중요 로직에 인라인 주석 추가
    - _코드 가독성 향상_

  - [ ] 7.2 배포 체크리스트 확인
    - 대상 채널 ID 설정 확인
    - 봇 권한 확인 (Read Message History, Send Messages, Embed Links)
    - forwarded.json 파일 권한 확인
    - 프로덕션 환경 테스트
    - _배포 준비 완료_

- [ ] 8. 최종 체크포인트 - 통합 검증
  - 모든 기능 재테스트
  - 오류 시나리오 테스트 (채널 없음, 권한 없음, 네트워크 오류)
  - 봇 재시작 후 중복 방지 확인
  - 성능 확인 (반응 이벤트 지연 시간)
  - 사용자에게 최종 승인 요청

## 참고 사항

### 작업 우선순위

- **필수 작업**: 1.1, 1.2, 2.1, 2.2, 3.1, 4.1, 4.2, 4.3, 4.4
- **선택 작업**: 6.1, 6.2, 6.3 (단위 테스트)
- **체크포인트**: 5, 8

### 테스트 전략

- 작업 5에서 수동 테스트로 기본 기능 검증
- 작업 6은 선택 사항이지만 장기 유지보수를 위해 권장됨
- 단위 테스트는 pytest와 pytest-asyncio 사용

### 중요 고려사항

1. **오류 처리**: 모든 외부 API 호출은 try-except로 보호
2. **로깅**: 모든 오류와 성공 메시지는 콘솔에 출력
3. **성능**: Set 자료구조로 O(1) 중복 확인
4. **데이터 지속성**: 포워딩 성공 시 즉시 JSON 저장

### 의존성

- py-cord >= 2.4.0 (이미 설치됨)
- python-dotenv >= 1.0.0 (이미 설치됨)
- pytest, pytest-asyncio (선택 작업 6을 위해 필요)

### 파일 구조

```
DiscordKeepBot_VibeCoding/
├── main.py                    # Intent 추가 필요
├── cogs/
│   └── reaction_forward.py    # 새로 생성
├── forwarded.json             # 자동 생성됨
└── tests/                     # 선택 사항
    └── test_reaction_forward.py
```

## 작업 의존성 그래프

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2"] },
    { "id": 1, "tasks": ["2.1"] },
    { "id": 2, "tasks": ["2.2"] },
    { "id": 3, "tasks": ["3.1"] },
    { "id": 4, "tasks": ["4.1", "4.2", "4.3"] },
    { "id": 5, "tasks": ["4.4"] },
    { "id": 6, "tasks": ["6.1", "6.2", "6.3"] },
    { "id": 7, "tasks": ["7.1", "7.2"] }
  ]
}
```
