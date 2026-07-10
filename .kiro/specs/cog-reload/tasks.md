# Implementation Plan: Cog Reload Feature

## Overview

This implementation plan breaks down the Cog reload feature into discrete coding tasks. The feature enables bot administrators to dynamically reload Discord bot Cog modules without restarting the bot process, accelerating development and debugging workflows.

**Implementation Approach:**
- Create new Admin cog with authorization and reload functionality
- Integrate environment variable configuration for admin authorization
- Implement both slash and message command interfaces
- Add comprehensive error handling with Korean user messages
- Follow existing project patterns (py-cord, cogs structure, dotenv)

## Tasks

- [ ] 1. Create Admin Cog structure and configuration parsing
  - [ ] 1.1 Create `cogs/admin.py` with Admin Cog class structure
    - Create new file `cogs/admin.py` with `Admin` class inheriting from `commands.Cog`
    - Implement `__init__` method that accepts bot instance
    - Add class docstring: "Admin commands for bot management"
    - Implement `setup(bot)` function at module level
    - _Requirements: 5.1, 5.2, 5.3_
  
  - [ ] 1.2 Implement admin configuration parsing from environment variables
    - Add instance variables: `admin_ids` (set[int]), `admin_role` (str | None)
    - Implement `_parse_admin_config()` method to parse `ADMIN_IDS` and `ADMIN_ROLE` from environment
    - Parse comma-separated `ADMIN_IDS` into set of integers with error handling
    - Parse `ADMIN_ROLE` as optional string
    - Add warning logs when environment variables are not set or parsing fails
    - _Requirements: 2.1, 2.2, 7.1, 7.2, 7.3, 7.4_

- [ ] 2. Implement authorization logic
  - [ ] 2.1 Create `is_admin()` authorization check method
    - Implement async method accepting `discord.ApplicationContext | commands.Context`
    - Check if `ctx.author.id` is in `self.admin_ids` set
    - Check if user has role matching `self.admin_role` (if configured)
    - Return `True` if either condition is met, `False` otherwise
    - Handle guild vs DM context (role check only in guilds)
    - _Requirements: 2.2, 2.3, 2.4, 1.3, 1.4_

- [ ] 3. Implement reload command handlers
  - [ ] 3.1 Implement slash command interface (`/reload`)
    - Add `@discord.slash_command` decorator with name="reload" and Korean description
    - Add `cog_name` parameter with `discord.Option` and Korean description
    - Call `is_admin()` and respond with "❌ 권한이 없습니다." (ephemeral) if unauthorized
    - Call `_reload_cog()` method with context and cog_name if authorized
    - _Requirements: 1.1, 1.3, 1.4, 5.4_
  
  - [ ] 3.2 Implement message command interface (`!reload`)
    - Add `@commands.command` decorator with name="reload"
    - Add `cog_name` parameter (str)
    - Call `is_admin()` and respond with "❌ 권한이 없습니다." if unauthorized
    - Call `_reload_cog()` method with context and cog_name if authorized
    - _Requirements: 1.2, 1.3, 1.4, 5.4_
  
  - [ ] 3.3 Implement `_build_extension_path()` static method
    - Accept `cog_name` parameter (str)
    - Return formatted string `f"cogs.{cog_name}"`
    - Add docstring explaining path construction
    - _Requirements: 6.1_

- [ ] 4. Implement core reload logic with error handling
  - [ ] 4.1 Implement `_reload_cog()` method
    - Accept context and cog_name parameters
    - Build extension path using `_build_extension_path()`
    - Check if extension path exists in `bot.extensions` before reload
    - If not loaded, respond with "❌ 존재하지 않는 Cog입니다: {cog_name}" and list available cogs
    - Call `bot.reload_extension(extension_path)` for valid extensions
    - Respond with "✅ {cog_name} 리로드 성공!" on success
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 6.2, 6.3_
  
  - [ ] 4.2 Add comprehensive exception handling to `_reload_cog()`
    - Catch `discord.ExtensionNotLoaded` and respond with "❌ 존재하지 않는 Cog입니다: {cog_name}"
    - Catch `discord.ExtensionFailed` and respond with "❌ 리로드 중 오류 발생:\n```python\n{error}\n```"
    - Catch `SyntaxError` specifically and respond with "❌ 문법 오류:\n```python\n{error}\n```"
    - Catch `ModuleNotFoundError` and respond with "❌ Cog 파일을 찾을 수 없습니다: {cog_name}"
    - Catch generic `Exception` and respond with "❌ 예기치 않은 오류:\n```python\n{type(e).__name__}: {e}\n```"
    - _Requirements: 4.1, 4.2, 4.3, 4.4_

- [ ] 5. Add logging and documentation
  - [ ] 5.1 Add logging throughout Admin cog
    - Import `logging` module and create logger for the module
    - Log INFO message on successful cog initialization with admin count
    - Log WARNING on unauthorized reload attempts with user ID
    - Log INFO on reload attempt with cog name and user ID
    - Log ERROR on reload failures with cog name and error details
    - _Requirements: 2.1, 3.1, 3.2, 4.3_
  
  - [ ] 5.2 Add comprehensive docstrings to all methods
    - Add docstrings to `__init__`, `_parse_admin_config`, `is_admin`, `reload_slash`, `reload_message`, `_reload_cog`, `_build_extension_path`
    - Include parameter descriptions and return value documentation
    - Add usage examples in `reload_slash` and `reload_message` docstrings
    - _Requirements: All_

- [ ] 6. Update environment configuration
  - [ ] 6.1 Add admin configuration to `.env` file
    - Add `ADMIN_IDS` variable with example comma-separated values (commented out with explanation)
    - Add `ADMIN_ROLE` variable with example role name (commented out with explanation)
    - Add comments explaining the format and purpose of each variable
    - _Requirements: 7.1, 7.2, 7.3_

- [ ] 7. Checkpoint - Test reload functionality manually
  - Start the bot and verify Admin cog loads successfully
  - Test `/reload` command with valid admin user and valid cog name
  - Test `/reload` command with unauthorized user
  - Test `!reload` message command with valid admin and valid cog
  - Test reload with invalid cog name
  - Create intentional syntax error in a test cog and verify error handling
  - Verify all Korean error messages display correctly
  - _Requirements: All_

- [ ] 8. Create unit tests for Admin cog
  - [ ]* 8.1 Create test file structure and fixtures
    - Create `tests/test_admin_cog.py` file
    - Import pytest, pytest-asyncio, unittest.mock
    - Create `mock_bot` fixture with mocked extensions dict
    - Create `mock_admin_context` fixture with admin user ID
    - Create `mock_user_context` fixture with non-admin user ID
    - Create temporary test cog file for integration testing
    - _Requirements: All (Testing)_
  
  - [ ]* 8.2 Write authorization tests
    - Test `is_admin()` returns True for valid admin ID
    - Test `is_admin()` returns True for valid admin role
    - Test `is_admin()` returns False for unauthorized user
    - Test authorization with both ID and role configured
    - Test authorization in DM context (no roles available)
    - _Requirements: 2.2, 2.3, 2.4_
  
  - [ ]* 8.3 Write configuration parsing tests
    - Test valid `ADMIN_IDS` parsed correctly into set
    - Test invalid `ADMIN_IDS` (non-numeric) handled gracefully
    - Test `ADMIN_IDS` with whitespace handled correctly
    - Test empty `ADMIN_IDS` logs warning
    - Test `ADMIN_ROLE` parsing
    - _Requirements: 7.1, 7.2, 7.3, 7.4_
  
  - [ ]* 8.4 Write extension path building tests
    - Test `_build_extension_path("general")` returns "cogs.general"
    - Test path building with underscores and special characters
    - Test path building with edge case cog names
    - _Requirements: 6.1_
  
  - [ ]* 8.5 Write error handling tests
    - Test `ExtensionNotLoaded` exception handling
    - Test `ExtensionFailed` exception handling
    - Test `SyntaxError` exception handling
    - Test `ModuleNotFoundError` exception handling
    - Test generic exception handling
    - Verify error messages are in Korean and formatted correctly
    - _Requirements: 4.1, 4.2, 4.3, 4.4_
  
  - [ ]* 8.6 Write integration tests
    - Test full reload flow with valid admin and valid cog
    - Test full authorization flow with unauthorized user
    - Test slash command and message command both work
    - Test reload of non-existent cog shows available cogs
    - Test concurrent reload attempts (thread safety)
    - _Requirements: All_

- [ ] 9. Update project documentation
  - [ ]* 9.1 Add Admin cog documentation to README (if exists)
    - Document `/reload` and `!reload` commands
    - Explain admin authorization setup
    - Provide example `.env` configuration
    - Add troubleshooting section for common issues
    - _Requirements: All_

- [ ] 10. Final checkpoint - Comprehensive testing
  - Run all unit tests with `pytest` and verify >85% coverage
  - Test the feature in real Discord environment with production bot
  - Verify all requirements from requirements.md are met
  - Test edge cases: self-reload of admin cog, empty cog name, concurrent reloads
  - Verify logging output is correct and helpful
  - Ensure all tests pass, ask the user if questions arise

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP deployment
- Each task references specific requirements from requirements.md for traceability
- The implementation follows existing project patterns (py-cord cogs structure, Korean messages)
- Checkpoints ensure incremental validation and user feedback opportunities
- Unit tests and integration tests are comprehensive but optional for initial deployment
- The Admin cog will be automatically loaded at bot startup like other cogs in the `cogs/` directory
- Authorization can be configured via either user IDs or role names (or both)
- All error messages are in Korean to match existing bot UI language

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2", "3.3"] },
    { "id": 2, "tasks": ["2.1"] },
    { "id": 3, "tasks": ["3.1", "3.2"] },
    { "id": 4, "tasks": ["4.1"] },
    { "id": 5, "tasks": ["4.2", "5.1"] },
    { "id": 6, "tasks": ["5.2", "6.1"] },
    { "id": 7, "tasks": ["8.1"] },
    { "id": 8, "tasks": ["8.2", "8.3", "8.4"] },
    { "id": 9, "tasks": ["8.5"] },
    { "id": 10, "tasks": ["8.6", "9.1"] }
  ]
}
```
